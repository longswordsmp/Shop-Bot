"""``/announce``: open the form, publish the submitted announcement, recover after a restart.

Flow:

1. ``open_form`` (slash command): validates the options (image, mention) and the target channel, stores a draft row
   with a random token and returns the form. Nothing is posted.
2. ``submit`` (form submission, routed by custom id ``sb:an:submit:<token>``; works after a restart): claims the draft
   atomically (a second submission of the same form can never post again), validates the text, the channel, the bot's
   permissions, the mention and the image, then posts one embed in the announcements channel. The uploaded image is
   downloaded again and re-attached to the message (``attachment://``) so it stays valid. Any problem means nothing is
   posted and staff get a private operator reply; most problems offer to reopen the form with the text already typed
   (``reopen_form``).
3. ``recover`` (startup): a row a previous process was posting when it stopped is marked ``interrupted`` (never
   retried automatically, since it may already have been posted) and recorded in the audit log.

Customers only ever see the published announcement. Nothing is posted except on a staff member's submission.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import aiohttp
import discord

from shopbot.config.content import MissingContent
from shopbot.core.permissions import CHANNEL_SEND_PERMISSIONS, missing_permissions
from shopbot.features.announcements import repo, views
from shopbot.features.announcements.content_keys import field_name_key
from shopbot.features.announcements.repo import Announcement, ImageRef
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_kwargs
from shopbot.ui.limits import EMBED_TOTAL_CHARS, validate_message
from shopbot.ui.spec import FileSpec, MessageSpec, ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

# A form (and the text typed into it, kept for "edit and try again") stays usable this long after /announce.
DRAFT_TTL_SECONDS = 6 * 3600
# Identifies this process in claimed rows, so recovery only touches rows a previous process left half-done.
PROCESS_TOKEN = secrets.token_hex(8)
TOKEN_RE = re.compile(r"^[0-9a-f]{24}$")
IMAGE_BASENAME = "announcement"
IMAGE_TYPES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
EXTENSION_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
CDN_PREFIXES = ("https://cdn.discordapp.com/", "https://media.discordapp.net/")
DOWNLOAD_TIMEOUT_SECONDS = 60
MAX_VIOLATIONS_SHOWN = 5


class AttachmentLike(Protocol):
    """What ``/announce`` reads from its ``image`` option (``discord.Attachment``)."""

    @property
    def filename(self) -> str: ...

    @property
    def url(self) -> str: ...

    @property
    def size(self) -> int: ...

    @property
    def content_type(self) -> str | None: ...


@dataclass(frozen=True)
class Problem:
    """Why an announcement cannot be posted (operator text). ``code`` goes to logs/audit; ``retryable`` problems
    offer to reopen the prefilled form."""

    text: str
    code: str
    retryable: bool = True


def staff_role_keys(app: App) -> tuple[str, ...]:
    """Roles allowed to announce besides ``permissions.staff_roles`` (and admins)."""
    return app.settings.announcements.roles


def new_token() -> str:
    return secrets.token_hex(12)


def _message_url(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _plain(text: str, limit: int = 100) -> str:
    """Untrusted names (file names, typed role names) shown inside operator text: no backticks, bounded."""
    cleaned = text.replace("`", "'").strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


# -- the form ---------------------------------------------------------------------------------------------------


def form_inputs(app: App) -> list[views.FormInput]:
    inputs: list[views.FormInput] = []
    for item in app.settings.announcements.fields:
        name = app.content.get(field_name_key(item.id))
        inputs.append(
            views.FormInput(
                field_id=item.id,
                label=views.field_label(app.op, item.id, name),
                style="paragraph" if item.style == "paragraph" else "short",
                required=item.required,
                max_length=item.max_length,
            )
        )
    return inputs


def upload_limit(app: App) -> int:
    """Largest file the bot may upload in the server (depends on the server's boost level)."""
    limit = getattr(app.guild, "filesize_limit", None)
    return limit if isinstance(limit, int) and limit > 0 else discord.utils.DEFAULT_FILE_SIZE_LIMIT_BYTES


def _megabytes(size: int) -> str:
    return f"{size / 1_048_576:.1f}"


def _declared_extension(content_type: str | None, filename: str) -> str | None:
    if content_type:
        return IMAGE_TYPES.get(content_type.split(";", 1)[0].strip().lower())
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return suffix if suffix in EXTENSION_TYPES else None


def check_attachment(app: App, attachment: AttachmentLike) -> ImageRef | Problem:
    op = app.op
    if not app.settings.announcements.allow_image:
        return Problem(op("announcements.error.image_disabled"), "image_disabled", retryable=False)
    extension = _declared_extension(attachment.content_type, attachment.filename)
    if extension is None or not attachment.url.startswith(CDN_PREFIXES):
        return Problem(
            op("announcements.error.image_type", filename=_plain(attachment.filename)), "image_type", retryable=False
        )
    limit = upload_limit(app)
    if attachment.size > limit:
        return Problem(
            op("announcements.error.image_too_large", size=_megabytes(attachment.size), limit=_megabytes(limit)),
            "image_too_large",
            retryable=False,
        )
    return ImageRef(
        url=attachment.url,
        filename=f"{IMAGE_BASENAME}.{extension}",
        content_type=EXTENSION_TYPES[extension],
        size=attachment.size,
    )


def _role(app: App, key: str) -> discord.Role | None:
    guild = app.guild
    role_id = app.resources.role_id(key)
    return guild.get_role(role_id) if guild is not None and role_id else None


def resolve_mention_key(app: App, raw: str | None) -> str | Problem | None:
    """The ``mention`` option: a key from ``announcements.mention_roles`` (autocomplete) or that role's name."""
    if raw is None or not raw.strip():
        return None
    allowed = app.settings.announcements.mention_roles
    if not allowed:
        return Problem(app.op("announcements.error.mention_none"), "mention_none", retryable=False)
    value = raw.strip()
    if value in allowed:
        return value
    lowered = value.casefold()
    for key in allowed:
        role = _role(app, key)
        if role is not None and role.name.casefold() == lowered:
            return key
    return Problem(
        app.op("announcements.error.mention_not_allowed", role=_plain(value)), "mention_not_allowed", retryable=False
    )


def target_channel(app: App, *, with_image: bool) -> discord.TextChannel | Problem:
    """The announcements channel, if it exists and the bot can post (and attach files) there."""
    op = app.op
    guild = app.guild
    if guild is None:
        return Problem(op("announcements.error.no_guild"), "no_guild")
    key = app.settings.announcements.channel
    channel = app.resources.text_channel(guild, key)
    if channel is None:
        return Problem(op("announcements.error.channel_missing", channel=key), "channel_missing")
    required = CHANNEL_SEND_PERMISSIONS + (("attach_files",) if with_image else ())
    missing = missing_permissions(guild.me, channel, required)
    if missing:
        return Problem(
            op("announcements.error.missing_permissions", permissions=", ".join(missing), channel=channel.mention),
            "missing_permissions",
        )
    return channel


def mention_target(app: App, channel: discord.TextChannel, key: str) -> discord.Role | Problem:
    """The role to ping: still allowed by the settings, created by /setup, and pingable by the bot in ``channel``."""
    op = app.op
    if key not in app.settings.announcements.mention_roles:
        return Problem(op("announcements.error.mention_not_allowed", role=key), "mention_not_allowed", retryable=False)
    role = _role(app, key)
    if role is None:
        return Problem(op("announcements.error.mention_missing", role=key), "mention_missing")
    if not role.mentionable and not channel.permissions_for(channel.guild.me).mention_everyone:
        return Problem(
            op("announcements.error.mention_not_pingable", role=f"<@&{role.id}>", channel=channel.mention),
            "mention_not_pingable",
        )
    return role


async def open_form(
    app: App, *, actor_id: int, image: AttachmentLike | None, mention: str | None
) -> ModalSpec | views.Reply:
    """Validate the slash command options and return the form (or a private error; nothing is stored then)."""
    problems: list[Problem] = []
    image_ref: ImageRef | None = None
    if image is not None:
        checked = check_attachment(app, image)
        if isinstance(checked, Problem):
            problems.append(checked)
        else:
            image_ref = checked
    mention_key: str | None = None
    resolved = resolve_mention_key(app, mention)
    if isinstance(resolved, Problem):
        problems.append(resolved)
    else:
        mention_key = resolved
    channel = target_channel(app, with_image=image is not None)
    if isinstance(channel, Problem):
        problems.append(channel)
    elif mention_key is not None:
        role = mention_target(app, channel, mention_key)
        if isinstance(role, Problem):
            problems.append(role)
    if problems:
        return views.error_reply(app.op, [p.text for p in problems])
    token = new_token()
    await app.db.transaction(
        _tx_create, app.env.guild_id, token, actor_id, mention_key, image_ref, app.clock.now(), DRAFT_TTL_SECONDS
    )
    return views.announcement_form(app.op, token=token, fields=form_inputs(app))


def _tx_create(
    conn: sqlite3.Connection,
    guild_id: int,
    token: str,
    actor_id: int,
    mention_key: str | None,
    image: ImageRef | None,
    now: int,
    ttl: int,
) -> Announcement:
    repo.purge_expired(conn, guild_id, now)
    return repo.create_draft(
        conn, guild_id, token=token, actor_id=actor_id, mention_role=mention_key, image=image, now=now, ttl_seconds=ttl
    )


def _state_reply(app: App, item: Announcement | None, actor_id: int, now: int) -> views.Reply | None:
    """Why a form can no longer be used, or None when it can."""
    op = app.op
    if item is None:
        return views.notice_reply(op, op("announcements.error.form_unknown"))
    if item.actor_id != actor_id:
        return views.notice_reply(op, op("announcements.error.not_owner"), tone="danger")
    if item.status == "published" and item.channel_id and item.message_id:
        return views.already_published_reply(op, url=_message_url(item.guild_id, item.channel_id, item.message_id))
    if item.status == "posting":
        return views.notice_reply(op, op("announcements.error.busy"), tone="info")
    if item.status == "interrupted":
        return views.notice_reply(op, op("announcements.error.interrupted", channel=_channel_text(app)))
    if item.expired(now):
        return views.notice_reply(op, op("announcements.error.expired", hours=DRAFT_TTL_SECONDS // 3600))
    return None


def _channel_text(app: App) -> str:
    key = app.settings.announcements.channel
    channel_id = app.resources.channel_id(key)
    return f"<#{channel_id}>" if channel_id else f"`{key}`"


async def reopen_form(app: App, *, token: str, actor_id: int) -> ModalSpec | views.Reply:
    """The form again for the "edit and try again" button, prefilled with what the failed attempt contained."""
    item = await app.db.run(repo.get_by_token, app.env.guild_id, token)
    blocked = _state_reply(app, item, actor_id, app.clock.now())
    if blocked is not None or item is None:
        return blocked or views.notice_reply(app.op, app.op("announcements.error.form_unknown"))
    defaults: dict[str, str] = {views.TITLE_INPUT: item.title or "", views.MESSAGE_INPUT: item.message or ""}
    for field_id, value in item.field_values.items():
        defaults[views.field_input_id(field_id)] = value
    return views.announcement_form(app.op, token=token, fields=form_inputs(app), defaults=defaults)


# -- publishing -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Submission:
    title: str
    message: str
    values: dict[str, str]  # configured field id -> typed value (may be empty)


def read_submission(app: App, fields: Mapping[str, str]) -> Submission:
    """The submitted form. Surrounding whitespace is removed; everything else is kept exactly as typed."""
    values = {
        item.id: fields.get(views.field_input_id(item.id), "").strip() for item in app.settings.announcements.fields
    }
    return Submission(
        title=fields.get(views.TITLE_INPUT, "").strip(),
        message=fields.get(views.MESSAGE_INPUT, "").strip(),
        values=values,
    )


def input_problems(app: App, submission: Submission) -> list[Problem]:
    op = app.op
    problems: list[Problem] = []
    if not submission.title:
        problems.append(Problem(op("announcements.error.title_empty"), "title_empty"))
    elif len(submission.title) > views.TITLE_MAX:
        problems.append(
            Problem(
                op("announcements.error.title_too_long", length=len(submission.title), limit=views.TITLE_MAX),
                "title_too_long",
            )
        )
    if not submission.message:
        problems.append(Problem(op("announcements.error.message_empty"), "message_empty"))
    elif len(submission.message) > views.MESSAGE_MAX:
        problems.append(
            Problem(
                op("announcements.error.message_too_long", length=len(submission.message), limit=views.MESSAGE_MAX),
                "message_too_long",
            )
        )
    for item in app.settings.announcements.fields:
        value = submission.values.get(item.id, "")
        label = views.field_label(op, item.id, app.content.get(field_name_key(item.id)))
        if item.required and not value:
            problems.append(Problem(op("announcements.error.field_required", field=label), "field_required"))
        elif len(value) > item.max_length:
            problems.append(
                Problem(
                    op("announcements.error.field_too_long", field=label, length=len(value), limit=item.max_length),
                    "field_too_long",
                )
            )
    return problems


def field_entries(app: App, values: Mapping[str, str]) -> list[tuple[str, str]]:
    """(configured heading, typed value) for every configured field that was filled in, in settings order."""
    entries: list[tuple[str, str]] = []
    for item in app.settings.announcements.fields:
        value = values.get(item.id, "")
        if not value:
            continue
        key = field_name_key(item.id)
        name = app.content.render(key)
        if name is None:
            raise MissingContent(key)
        entries.append((name, value))
    return entries


def limit_problems(app: App, spec: MessageSpec) -> list[Problem]:
    violations = validate_message(spec)
    if not violations:
        return []
    total = sum(embed.text_length() for embed in spec.embeds)
    if total > EMBED_TOTAL_CHARS:
        return [
            Problem(
                app.op("announcements.error.total_too_long", length=total, limit=EMBED_TOTAL_CHARS), "total_too_long"
            )
        ]
    shown = "; ".join(str(v) for v in violations[:MAX_VIOLATIONS_SHOWN])
    return [Problem(app.op("announcements.error.limits", violations=shown), "limits")]


def sniff_image(data: bytes) -> str | None:
    """The image format from the file's first bytes (the declared type of an upload is not trusted)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


async def download_image(app: App, image: ImageRef) -> tuple[bytes, str] | Problem:
    """Download the uploaded image again; returns (bytes, upload filename)."""
    op = app.op
    http = getattr(app.bot, "http", None)
    if http is None or not image.url.startswith(CDN_PREFIXES):
        return Problem(op("announcements.error.image_download"), "image_download")
    try:
        data = await asyncio.wait_for(http.get_from_cdn(image.url), DOWNLOAD_TIMEOUT_SECONDS)
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError) as exc:
        log_event(log, logging.WARNING, "announcement_image_download_failed", error=type(exc).__name__)
        return Problem(op("announcements.error.image_download"), "image_download")
    if not isinstance(data, bytes):
        return Problem(op("announcements.error.image_download"), "image_download")
    limit = upload_limit(app)
    if len(data) > limit:
        return Problem(
            op("announcements.error.image_too_large", size=_megabytes(len(data)), limit=_megabytes(limit)),
            "image_too_large",
            retryable=False,
        )
    extension = sniff_image(data)
    if extension is None:
        return Problem(op("announcements.error.image_type", filename=image.filename), "image_type", retryable=False)
    return data, f"{IMAGE_BASENAME}.{extension}"


@dataclass(frozen=True)
class Posted:
    message: discord.Message
    channel: discord.TextChannel
    role: discord.Role | None
    has_image: bool


async def _post(app: App, item: Announcement, submission: Submission) -> Posted | list[Problem]:
    """Validate everything, then post. Returns the problems (nothing posted) or the posted message."""
    problems = input_problems(app, submission)
    channel = target_channel(app, with_image=item.image is not None)
    if isinstance(channel, Problem):
        problems.append(channel)
    role: discord.Role | Problem | None = None
    if item.mention_role and not isinstance(channel, Problem):
        role = mention_target(app, channel, item.mention_role)
        if isinstance(role, Problem):
            problems.append(role)
    if item.image is not None and not app.settings.announcements.allow_image:
        problems.append(Problem(app.op("announcements.error.image_disabled"), "image_disabled", retryable=False))
    if problems or isinstance(channel, Problem) or isinstance(role, Problem):
        return problems
    ping = role

    def build(image: FileSpec | None) -> MessageSpec:
        return views.announcement_message(
            app.ui,
            title=submission.title,
            message=submission.message,
            fields=field_entries(app, submission.values),
            image=image,
            mention_role_id=ping.id if ping is not None else None,
        )

    # Checked before downloading: a reference (no bytes yet) with the upload's size stands in for the image.
    reference = (
        FileSpec(
            filename=f"{IMAGE_BASENAME}.png", sha256="", size=item.image.size, content_type=item.image.content_type
        )
        if item.image is not None
        else None
    )
    problems = limit_problems(app, build(reference))
    if problems:
        return problems
    upload: tuple[bytes, str] | None = None
    if item.image is not None:
        downloaded = await download_image(app, item.image)
        if isinstance(downloaded, Problem):
            return [downloaded]
        upload = downloaded
    spec = build(FileSpec.of(upload[1], upload[0]) if upload else None)
    try:
        message = await channel.send(**send_kwargs(spec))
    except RenderError as exc:
        shown = "; ".join(str(v) for v in exc.violations[:MAX_VIOLATIONS_SHOWN])
        return [Problem(app.op("announcements.error.limits", violations=shown), "limits")]
    except discord.Forbidden as exc:
        log_event(log, logging.WARNING, "announcement_forbidden", channel_id=channel.id, code=exc.code)
        return [Problem(app.op("announcements.error.forbidden", channel=channel.mention), "forbidden")]
    except discord.HTTPException as exc:
        log_event(
            log, logging.WARNING, "announcement_send_failed", channel_id=channel.id, status=exc.status, code=exc.code
        )
        return [
            Problem(app.op("announcements.error.send_failed", http_status=exc.status, code=exc.code), "send_failed")
        ]
    return Posted(message=message, channel=channel, role=ping, has_image=upload is not None)


# Problems that come from the server state (channel, permissions, Discord) rather than from what staff typed.
_AUDITED_PROBLEMS = frozenset(
    {
        "no_guild",
        "channel_missing",
        "missing_permissions",
        "mention_missing",
        "mention_not_pingable",
        "image_download",
        "forbidden",
        "send_failed",
        "limits",
    }
)


async def submit(app: App, *, token: str, actor_id: int, fields: Mapping[str, str]) -> views.Reply:
    """Publish a submitted form. Never posts twice for one form; never posts anything incomplete."""
    submission = read_submission(app, fields)
    guild_id = app.env.guild_id
    outcome, item = await app.db.transaction(
        repo.claim,
        guild_id,
        token,
        actor_id=actor_id,
        claimed_by=PROCESS_TOKEN,
        title=submission.title,
        message=submission.message,
        field_values=submission.values,
        now=app.clock.now(),
    )
    if outcome != "claimed" or item is None:
        return _state_reply(app, item, actor_id, app.clock.now()) or views.notice_reply(
            app.op, app.op("announcements.error.form_unknown")
        )
    posted: Posted | list[Problem] = []
    try:
        posted = await _post(app, item, submission)
    finally:
        if not isinstance(posted, Posted):
            # Nothing was posted (or an unexpected error happened before posting): the form may be retried.
            await app.db.transaction(repo.release, item.id, PROCESS_TOKEN, app.clock.now())
    if not isinstance(posted, Posted):
        return await _failure_reply(app, item, posted)
    return await _published(app, item, submission, posted)


async def _failure_reply(app: App, item: Announcement, problems: list[Problem]) -> views.Reply:
    codes = sorted({p.code for p in problems})
    log_event(log, logging.INFO, "announcement_not_posted", announcement_id=item.id, problems=codes)
    audited = [code for code in codes if code in _AUDITED_PROBLEMS]
    if audited:
        try:
            await app.audit.record(
                "announcements.failed",
                actor_id=item.actor_id,
                target_type="announcement",
                target_id=item.id,
                details={"problems": audited},
            )
        except Exception:
            log.exception("audit of a failed announcement failed")
    retryable = all(p.retryable for p in problems)
    return views.error_reply(app.op, [p.text for p in problems], retry_token=item.token if retryable else None)


async def _published(app: App, item: Announcement, submission: Submission, posted: Posted) -> views.Reply:
    message, channel = posted.message, posted.channel
    recorded = await app.db.transaction(
        repo.mark_published,
        item.id,
        PROCESS_TOKEN,
        channel_id=channel.id,
        message_id=message.id,
        now=app.clock.now(),
    )
    if not recorded:
        log_event(log, logging.WARNING, "announcement_record_lost", announcement_id=item.id, message_id=message.id)
    await app.db.transaction(repo.purge_expired, app.env.guild_id, app.clock.now())
    try:
        await app.audit.record(
            "announcements.published",
            actor_id=item.actor_id,
            target_type="message",
            target_id=message.id,
            details={
                "announcement_id": item.id,
                "channel_id": channel.id,
                "title_length": len(submission.title),
                "message_length": len(submission.message),
                "fields": [field_id for field_id, value in submission.values.items() if value],
                "image": posted.has_image,
                "mention_role": item.mention_role,
            },
        )
    except Exception:
        log.exception("audit of a published announcement failed")
    url = getattr(message, "jump_url", None) or _message_url(app.env.guild_id, channel.id, message.id)
    return views.published_reply(
        app.op,
        channel_id=channel.id,
        url=url,
        mention_role_id=posted.role.id if posted.role is not None else None,
        has_image=posted.has_image,
    )


# -- restart recovery -------------------------------------------------------------------------------------------


async def recover(app: App) -> None:
    """Startup: rows a previous process was posting are marked interrupted (and audited); expired forms purged."""
    now = app.clock.now()
    interrupted = await app.db.transaction(repo.mark_interrupted, app.env.guild_id, PROCESS_TOKEN, now)
    for item in interrupted:
        log_event(log, logging.WARNING, "announcement_interrupted", announcement_id=item.id)
        await app.audit.record(
            "announcements.interrupted",
            actor_id=item.actor_id,
            target_type="announcement",
            target_id=item.id,
            details={"channel": app.settings.announcements.channel},
        )
    await app.db.transaction(repo.purge_expired, app.env.guild_id, now)
