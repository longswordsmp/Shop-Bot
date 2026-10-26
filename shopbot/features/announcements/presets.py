"""``/announce preset:<id>``: a ready-made announcement (``[[announcements.presets]]``, texts
``announcements.preset.<id>.*``) posted after a private preview, with the claim button of its coupon.

Flow:

1. ``open_preview`` (the slash command with ``preset``): checks the options (image, mention), the channel, the
   preset's texts and channel links and, for a preset with a coupon, that the coupon can be claimed now (discount codes
   on, the code exists and is not ended, expired or used up, and this preset did not announce it already). The staff
   member gets two private messages: the announcement exactly as it will be posted (its claim button disabled) and a
   card with the channel, the mention and the coupon's terms in plain words. With a problem the card says what to fix
   (e.g. the exact ``/discount create`` command), has no Post button and nothing is stored; otherwise a draft row is
   stored (``repo``, ``preset_id`` set, with the code the preview showed) and the card has Post announcement and
   Cancel.
2. ``post`` (``sb:an:post:<token>``; works after a restart): checks everything again at that moment, claims the row
   (one preview posts once: a double press gets "being posted" or "already posted"; a second preview of the same
   offer, e.g. by another staff member, is refused as already announced; a coupon that was ended and created again
   since the preview is refused, since its terms may differ from what the preview showed), posts the announcement
   with its claim button, records the message with its code, audits it and edits the card into a confirmation with a
   link. Nothing posted: the row is released and the card shows the problems (with Post announcement again when the
   problem was in the server, e.g. a permission; a coupon problem needs /announce again). A code that ended while the
   announcement was being posted gets its button switched right after (step 4).
3. ``cancel`` (``sb:an:cancel:<token>``): deletes the draft; the card says nothing was posted.
4. The offer ending: ``/discount end`` publishes ``DISCOUNT_CODE_ENDED`` and a claim press that finds the code ended
   or expired publishes ``DISCOUNT_OFFER_ENDED``. Both schedule ``announcements.offer_ended`` (a durable task, one per
   announcement) that edits the posted claim button into the disabled "offer ended" label and records it.
   ``recover`` (startup) schedules it for every posted offer whose code ended or expired meanwhile (an offer that
   expired while nobody pressed the button, or a stop before the event was handled). A task that failed for good
   (e.g. the bot may not edit the message) is tried again by ``/discount end`` and at startup, not by every later
   press of the button. A code that is used up does not switch the button: a use comes back when an unpaid order is
   cancelled. A code deleted by ``/reset`` leaves the button as it is; pressing it answers that the offer has ended.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord

from shopbot.config.content import MissingContent
from shopbot.config.settings import AnnouncementPreset
from shopbot.core.scheduler import PermanentTaskError
from shopbot.db.repos.tasks import Task
from shopbot.features.announcements import repo, service, views
from shopbot.features.announcements.content_keys import (
    PRESET_DESCRIPTION,
    PRESET_ENDED_BUTTON,
    preset_key,
)
from shopbot.features.announcements.repo import Announcement, ImageRef
from shopbot.features.announcements.service import AttachmentLike, Problem
from shopbot.features.checkout import discount_codes, discount_repo
from shopbot.features.checkout import discounts as d
from shopbot.features.server_keys import channel_placeholder
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import to_view
from shopbot.ui.spec import FileSpec, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

OFFER_ENDED_TASK = "announcements.offer_ended"
CHECKOUT = "checkout"
_CHANNEL_LINK = re.compile(r"\{(channel_[a-z0-9_]+)\}")
CHOICES_MAX = 25


def offer_ended_dedupe(announcement_id: int) -> str:
    return f"{OFFER_ENDED_TASK}:{announcement_id}"


def find_preset(app: App, value: str) -> AnnouncementPreset | None:
    """The ``preset`` option: a preset's id (autocomplete) or its name."""
    presets = app.settings.announcements.presets
    text = value.strip()
    found = next((p for p in presets if p.id == text), None)
    lowered = text.casefold()
    return found or next((p for p in presets if p.name.casefold() == lowered), None)


def choices(app: App, current: str) -> list[tuple[str, str]]:
    """(name, id) of the configured presets whose name or id contains ``current`` (autocomplete)."""
    query = current.strip().casefold()
    found = [
        (p.name, p.id)
        for p in app.settings.announcements.presets
        if not query or query in p.name.casefold() or query in p.id
    ]
    return found[:CHOICES_MAX]


# -- checks shared by the preview and Post --------------------------------------------------------------------------


def links(app: App) -> dict[str, str]:
    """``channel_<key>`` -> ``<#id>`` for every configured channel that exists in the server now."""
    guild = app.guild
    values: dict[str, str] = {}
    for channel in app.settings.server.channels:
        channel_id = app.resources.channel_id(channel.key)
        if guild is not None and channel_id and guild.get_channel(channel_id) is not None:
            values[channel_placeholder(channel.key)] = f"<#{channel_id}>"
    return values


def text_problems(app: App, preset: AnnouncementPreset, values: dict[str, str]) -> list[Problem]:
    """The preset's channel links whose channel does not exist (the text would show a blank instead of a link)."""
    known = {channel_placeholder(channel.key) for channel in app.settings.server.channels}
    template = app.content.get(preset_key(preset.id, PRESET_DESCRIPTION)) or ""
    missing = sorted({name for name in _CHANNEL_LINK.findall(template) if name in known and name not in values})
    return [
        Problem(app.op("announcements.preset.link_missing", channel=name.removeprefix("channel_")), "link_missing")
        for name in missing
    ]


def create_command(app: App, preset: AnnouncementPreset) -> str:
    """How staff create the preset's coupon: its ``coupon_command`` (code formatting), or a generic hint."""
    if preset.coupon_command:
        return "`" + preset.coupon_command.replace("`", "'") + "`"
    return app.op("announcements.preset.coupon_command", code=preset.coupon)


async def coupon_check(app: App, preset: AnnouncementPreset) -> tuple[d.Code | None, list[Problem]]:
    """The live code of the preset's coupon, or why its claim button would not work now (each problem names the fix).
    A preset without a coupon has neither."""
    if not preset.coupon:
        return None, []
    op = app.op
    if not app.settings.checkout.discount_codes:
        return None, [Problem(op("announcements.preset.coupons_off"), "coupons_off", retryable=False)]
    if not app.ready(CHECKOUT):
        return None, [Problem(op("announcements.preset.checkout_off"), "checkout_off", retryable=False)]
    code = await discount_codes.usable_code(app, preset.coupon)
    if code is None:
        live = await discount_codes.find_code(app, preset.coupon)
        if live is not None and not live.ended and live.expired(app.clock.now()):
            # Still live by name: /discount create would refuse it until it is ended.
            key, problem = "announcements.preset.coupon_expired", "coupon_expired"
        else:
            key, problem = "announcements.preset.coupon_missing", "coupon_missing"
        text = op(key, code=preset.coupon, command=create_command(app, preset))
        return None, [Problem(text, problem, retryable=False)]
    if code.max_uses is not None:
        usage = await app.db.run(discount_repo.usage, code.id, 0)
        if usage.uses >= code.max_uses:
            text = op("announcements.preset.coupon_used_up", code=code.code, uses=code.max_uses)
            return code, [Problem(text, "coupon_used_up", retryable=False)]
    return code, []


async def _message_gone(app: App, item: Announcement) -> bool:
    """Whether a posted announcement's message was deleted (its channel too). Unknown (Discord unavailable) = no."""
    guild = app.guild
    if guild is None or item.channel_id is None or item.message_id is None:
        return False
    channel = guild.get_channel(item.channel_id)
    if channel is None:
        return True
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        await channel.fetch_message(item.message_id)
    except discord.NotFound:
        return True
    except discord.HTTPException:
        return False
    return False


async def announced_problem(app: App, preset: AnnouncementPreset, code: d.Code) -> Problem | None:
    """This preset already announced this code (posting or posted): one announcement per offer. A posted
    announcement whose message was deleted in Discord no longer counts."""
    other = await app.db.run(repo.announced, app.env.guild_id, preset.id, code.id)
    if other is None:
        return None
    if other.status == "posting":
        return Problem(app.op("announcements.error.busy"), "busy", retryable=False)
    if await _message_gone(app, other):
        await app.db.transaction(repo.forget_offer, other.id, app.clock.now())
        log_event(log, logging.INFO, "announcement_offer_forgotten", announcement_id=other.id, reason="deleted")
        return None
    url = service.message_url(other.guild_id, other.channel_id or 0, other.message_id or 0)
    text = app.op("announcements.preset.already_announced", code=code.code)
    return Problem(text, "already_announced", retryable=False, url=url)


def _announcement(
    app: App,
    preset: AnnouncementPreset,
    values: dict[str, str],
    code: d.Code | None,
    *,
    preview: bool,
    image: FileSpec | None = None,
    image_url: str | None = None,
    role_id: int | None = None,
) -> MessageSpec | Problem:
    try:
        return views.preset_announcement(
            app.ui,
            preset,
            links=values,
            code_id=code.id if code is not None else None,
            preview=preview,
            image=image,
            image_url=image_url,
            mention_role_id=role_id,
        )
    except MissingContent as exc:
        return Problem(app.op("announcements.preset.text_missing", key=exc.key), "text_missing", retryable=False)


# -- the preview (slash command) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Preview:
    """What ``/announce preset:`` answers privately: the announcement as it will be posted (None when it cannot be
    built) and the card under it."""

    announcement: MessageSpec | None
    card: views.Reply


async def open_preview(
    app: App, *, actor_id: int, preset: str, image: AttachmentLike | None, mention: str | None
) -> Preview:
    """Check everything a Post would need and return the private preview (see the module docstring)."""
    op = app.op
    chosen = find_preset(app, preset)
    if chosen is None:
        unknown = op("announcements.preset.unknown_preset", preset=service.plain(preset))
        return Preview(None, views.error_reply(op, [unknown]))
    problems: list[Problem] = []
    image_ref: ImageRef | None = None
    if image is not None:
        checked = service.check_attachment(app, image, chosen.banner)
        if isinstance(checked, Problem):
            problems.append(checked)
        else:
            image_ref = checked
    mention_key: str | None = None
    resolved = service.resolve_mention_key(app, mention)
    if isinstance(resolved, Problem):
        problems.append(resolved)
    else:
        mention_key = resolved
    channel = service.target_channel(app, with_image=image is not None, banner=chosen.banner)
    role: discord.Role | None = None
    if isinstance(channel, Problem):
        problems.append(channel)
    elif mention_key is not None:
        target = service.mention_target(app, channel, mention_key)
        if isinstance(target, Problem):
            problems.append(target)
        else:
            role = target
    values = links(app)
    problems += text_problems(app, chosen, values)
    code, coupon_problems = await coupon_check(app, chosen)
    problems += coupon_problems
    if code is not None and not coupon_problems:
        announced = await announced_problem(app, chosen, code)
        if announced is not None:
            problems.append(announced)
    # The copy shows the uploaded picture from its link; the post attaches it (``service.send_announcement``).
    copy = _announcement(
        app, chosen, values, code, preview=True, image_url=image.url if image is not None and image_ref else None
    )
    shown: MessageSpec | None = None
    if isinstance(copy, Problem):
        problems.append(copy)
    else:
        too_long = service.limit_problems(app, copy)
        problems += too_long
        shown = None if too_long else copy
    terms = await discount_codes.terms_text(app, code) if code is not None else None
    facts = views.preset_facts(
        op,
        channel=service.channel_text(app),
        mention_role_id=role.id if role is not None else None,
        has_image=image_ref is not None,
        coupon=(code.code, terms) if code is not None and terms is not None else None,
    )
    if problems:
        card = views.preset_problem_reply(
            op,
            name=chosen.name,
            preset_id=chosen.id,
            facts=facts,
            problems=[p.text for p in problems],
            link_url=next((p.url for p in problems if p.url), None),
        )
        return Preview(shown, card)
    token = service.new_token()
    code_id = code.id if code is not None else None
    await app.db.transaction(
        _tx_create, app.env.guild_id, token, actor_id, mention_key, image_ref, chosen.id, code_id, app.clock.now()
    )
    return Preview(shown, views.preset_preview_reply(op, name=chosen.name, facts=facts, token=token))


def _tx_create(
    conn: sqlite3.Connection,
    guild_id: int,
    token: str,
    actor_id: int,
    mention_key: str | None,
    image: ImageRef | None,
    preset_id: str,
    code_id: int | None,
    now: int,
) -> Announcement:
    repo.purge_expired(conn, guild_id, now)
    return repo.create_draft(
        conn,
        guild_id,
        token=token,
        actor_id=actor_id,
        mention_role=mention_key,
        image=image,
        now=now,
        ttl_seconds=service.DRAFT_TTL_SECONDS,
        preset_id=preset_id,
        discount_code_id=code_id,
    )


# -- Post announcement and Cancel ------------------------------------------------------------------------------------


async def post(app: App, *, token: str, actor_id: int) -> views.Reply:
    """Post a preset's announcement from its preview. Never posts twice for one preview, nor one offer twice."""
    op = app.op
    guild_id = app.env.guild_id
    item = await app.db.run(repo.get_by_token, guild_id, token)
    if item is None or item.preset_id is None:
        return service.unknown_preview_reply(app)
    blocked = service.state_reply(app, item, actor_id, app.clock.now())
    if blocked is not None:
        return blocked
    preset = app.settings.announcements.preset(item.preset_id)
    if preset is None:
        removed = op("announcements.preset.removed", preset=item.preset_id)
        return views.preset_error_reply(op, [removed], token=None, preset_id=item.preset_id)
    code, problems = await coupon_check(app, preset)
    if problems:
        return views.preset_error_reply(op, [p.text for p in problems], token=None, preset_id=preset.id)
    outcome, claimed = await app.db.transaction(
        repo.claim,
        guild_id,
        token,
        actor_id=actor_id,
        claimed_by=service.PROCESS_TOKEN,
        now=app.clock.now(),
        discount_code_id=code.id if code is not None else None,
        preset=True,
    )
    if outcome == "changed" and code is not None:
        changed = op("announcements.preset.coupon_changed", code=code.code)
        return views.preset_error_reply(op, [changed], token=None, preset_id=preset.id)
    if outcome == "announced" and claimed is not None and code is not None:
        if claimed.status == "posting":
            return views.preset_error_reply(op, [op("announcements.error.busy")], token=None, preset_id=preset.id)
        url = service.message_url(claimed.guild_id, claimed.channel_id or 0, claimed.message_id or 0)
        line = op("announcements.preset.already_announced", code=code.code)
        return views.preset_error_reply(op, [line], token=None, preset_id=preset.id, link_url=url)
    if outcome != "claimed" or claimed is None:
        return service.state_reply(app, claimed, actor_id, app.clock.now()) or service.unknown_preview_reply(app)
    posted: service.Posted | list[Problem] = []
    try:
        posted = await _post(app, claimed, preset, code)
    finally:
        if not isinstance(posted, service.Posted):
            # Nothing was posted (or an unexpected error happened before posting): Post may be pressed again.
            await app.db.transaction(repo.release, claimed.id, service.PROCESS_TOKEN, app.clock.now())
    if not isinstance(posted, service.Posted):
        return await _failure_reply(app, claimed, preset, posted)
    return await _published(app, claimed, preset, code, posted)


async def _post(
    app: App, item: Announcement, preset: AnnouncementPreset, code: d.Code | None
) -> service.Posted | list[Problem]:
    values = links(app)
    problems = text_problems(app, preset, values)
    texts = _announcement(app, preset, values, code, preview=False)  # the texts are checked before anything else
    if isinstance(texts, Problem):
        problems.append(texts)

    def build(image: FileSpec | None, role_id: int | None) -> MessageSpec:
        return views.preset_announcement(
            app.ui,
            preset,
            links=values,
            code_id=code.id if code is not None else None,
            image=image,
            mention_role_id=role_id,
        )

    return await service.send_announcement(app, item, build, problems)


async def _failure_reply(
    app: App, item: Announcement, preset: AnnouncementPreset, problems: list[Problem]
) -> views.Reply:
    codes = sorted({p.code for p in problems})
    log_event(log, logging.INFO, "announcement_not_posted", announcement_id=item.id, problems=codes)
    audited = [code for code in codes if code in service.AUDITED_PROBLEMS]
    if audited:
        try:
            await app.audit.record(
                "announcements.failed",
                actor_id=item.actor_id,
                target_type="announcement",
                target_id=item.id,
                details={"problems": audited, "preset": preset.id},
            )
        except Exception:
            log.exception("audit of a failed announcement failed")
    retryable = all(p.retryable for p in problems)
    token = item.token if retryable else None
    return views.preset_error_reply(app.op, [p.text for p in problems], token=token, preset_id=preset.id)


async def _published(
    app: App, item: Announcement, preset: AnnouncementPreset, code: d.Code | None, posted: service.Posted
) -> views.Reply:
    message, channel = posted.message, posted.channel
    recorded = await app.db.transaction(
        repo.mark_published,
        item.id,
        service.PROCESS_TOKEN,
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
                "preset": preset.id,
                "coupon": code.code if code is not None else None,
                "discount_code_id": code.id if code is not None else None,
                "image": posted.has_image,
                "mention_role": item.mention_role,
            },
        )
    except Exception:
        log.exception("audit of a published announcement failed")
    url = getattr(message, "jump_url", None) or service.message_url(app.env.guild_id, channel.id, message.id)
    coupon = None
    if code is not None:
        current = await app.db.run(discount_repo.get_code, app.env.guild_id, code.id)
        if recorded and current is not None and not current.usable(app.clock.now()):
            # The code ended (or expired) while the announcement was being posted: /discount end found no posted
            # announcement to switch then, so the button is switched now.
            await schedule_offer_ended(app, item.id)
            coupon = app.op("announcements.preset.ended_while_posting", code=code.code)
        else:
            coupon = views.coupon_line(app.op, code.code, await discount_codes.terms_text(app, code))
    return views.published_reply(
        app.op,
        channel_id=channel.id,
        url=url,
        mention_role_id=posted.role.id if posted.role is not None else None,
        has_image=posted.has_image,
        coupon=coupon,
    )


async def cancel(app: App, *, token: str, actor_id: int) -> views.Reply:
    """Cancel a preset's preview: its draft is deleted (nothing was posted)."""
    guild_id = app.env.guild_id
    if await app.db.transaction(repo.cancel, guild_id, token, actor_id):
        log_event(log, logging.INFO, "announcement_preview_cancelled", actor_id=actor_id)
        return views.cancelled_reply(app.op)
    item = await app.db.run(repo.get_by_token, guild_id, token)
    if item is None or item.preset_id is None:
        return service.unknown_preview_reply(app)
    return service.state_reply(app, item, actor_id, app.clock.now()) or service.unknown_preview_reply(app)


# -- the offer ending -------------------------------------------------------------------------------------------------


async def schedule_offer_ended(app: App, announcement_id: int, *, retry_failed: bool = True) -> None:
    """Schedule the switch of a posted claim button to "offer ended". ``retry_failed``: a task that failed for good
    earlier (e.g. the bot may not edit the message) is tried again; without it an existing task is left as it is."""
    await app.scheduler.schedule(
        OFFER_ENDED_TASK,
        run_at=app.clock.now(),
        payload={"announcement_id": announcement_id},
        dedupe_key=offer_ended_dedupe(announcement_id),
        replace=retry_failed,
    )


async def on_offer_ended(app: App, *, code_id: int, retry_failed: bool = True, **_: Any) -> None:
    """``DISCOUNT_CODE_ENDED`` (``retry_failed``) / ``DISCOUNT_OFFER_ENDED`` (published on every press of an ended
    claim button, so it never restarts a failed task: one failure, one alert): schedule the switch of every posted
    claim button of this code to "offer ended". Never raises."""
    try:
        for item in await app.db.run(repo.open_offers, app.env.guild_id, code_id):
            await schedule_offer_ended(app, item.id, retry_failed=retry_failed)
    except Exception:
        log.exception("scheduling the end of an announced offer failed (code %s)", code_id)


async def run_offer_ended_task(app: App, task: Task) -> None:
    """``announcements.offer_ended`` (payload ``announcement_id``): edit the posted claim button into the disabled
    "offer ended" label (or remove it when that label is not configured any more) and record it. Idempotent. A
    message or channel that is gone counts as done; Discord errors are retried with the scheduler's backoff."""
    announcement_id = task.payload.get("announcement_id")
    if isinstance(announcement_id, bool) or not isinstance(announcement_id, int) or announcement_id <= 0:
        raise PermanentTaskError("invalid payload: announcement_id must be a positive integer")
    item = await app.db.run(repo.get_by_id, announcement_id)
    if item is None or item.status != "published" or item.offer_ended_at is not None or item.discount_code_id is None:
        return
    await switch_to_ended(app, item)


def _ended_view(app: App, item: Announcement) -> discord.ui.View | None:
    preset = app.settings.announcements.preset(item.preset_id or "")
    key = preset_key(preset.id, PRESET_ENDED_BUTTON) if preset is not None else None
    button = None
    if key is not None and key in app.content.registry and item.discount_code_id is not None:
        button = views.ended_button(app.ui, key, item.discount_code_id)
    if button is None:
        log_event(log, logging.WARNING, "announcement_offer_ended_label_missing", announcement_id=item.id)
        return None  # the claim button is removed: it would only say the offer has ended
    return to_view(MessageSpec(rows=((button,),)))


async def switch_to_ended(app: App, item: Announcement) -> None:
    guild = app.guild
    if guild is None:
        raise RuntimeError("the server is not available")  # retried
    channel = guild.get_channel(item.channel_id or 0)
    if isinstance(channel, discord.TextChannel) and item.message_id is not None:
        try:
            # Only the components change; the text, the banner and the image stay as posted.
            await channel.get_partial_message(item.message_id).edit(view=_ended_view(app, item))
        except discord.NotFound:
            log_event(log, logging.INFO, "announcement_offer_ended_message_gone", announcement_id=item.id)
        except discord.Forbidden as exc:
            raise PermanentTaskError(f"cannot edit the announcement (Discord code {exc.code})") from exc
    else:
        log_event(log, logging.INFO, "announcement_offer_ended_channel_gone", announcement_id=item.id)
    await app.db.transaction(repo.mark_offer_ended, item.id, app.clock.now())
    log_event(log, logging.INFO, "announcement_offer_ended", announcement_id=item.id, code_id=item.discount_code_id)


async def recover(app: App) -> None:
    """Startup: switch the claim buttons of posted offers whose code ended or expired while the bot was not watching."""
    for item in await app.db.run(repo.ended_offers, app.env.guild_id, app.clock.now()):
        await schedule_offer_ended(app, item.id)
