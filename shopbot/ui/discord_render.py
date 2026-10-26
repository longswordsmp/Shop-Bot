"""Convert UI specs into discord.py objects and send/edit them safely.

Every outgoing message is validated against Discord limits first; a spec that violates a limit raises
``RenderError`` and nothing is sent (text is never truncated). Mentions are disabled by default and
enabled only for the user/role IDs a spec explicitly lists. The banner is placed (``ui.banners.place_banner``)
before the spec is checked and converted, so what is checked is what is sent.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from typing import Any

import discord

from shopbot.core.clock import to_datetime
from shopbot.logging_setup import log_event
from shopbot.ui.banners import banner_inside, is_banner_filename, place_banner
from shopbot.ui.component_emoji import split_button, split_option, strip_custom_emoji
from shopbot.ui.limits import Violation, message_files, validate_message, validate_modal
from shopbot.ui.spec import ButtonSpec, EmbedSpec, FileSpec, MessageSpec, ModalSpec, SelectSpec

log = logging.getLogger(__name__)

_BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
    "link": discord.ButtonStyle.link,
}


class RenderError(Exception):
    def __init__(self, violations: list[Violation]) -> None:
        super().__init__("; ".join(str(v) for v in violations))
        self.violations = violations


def check_message(spec: MessageSpec) -> None:
    violations = validate_message(spec)
    if violations:
        log_event(log, logging.ERROR, "render_limits_violated", violations=[str(v) for v in violations][:20])
        raise RenderError(violations)


def check_modal(spec: ModalSpec) -> None:
    violations = validate_modal(spec)
    if violations:
        log_event(log, logging.ERROR, "render_limits_violated", violations=[str(v) for v in violations][:20])
        raise RenderError(violations)


def parse_emoji(value: str | None) -> discord.PartialEmoji | None:
    if not value:
        return None
    return discord.PartialEmoji.from_str(value)


def to_embed(spec: EmbedSpec) -> discord.Embed:
    """The Discord embed of ``spec``. Custom emoji are left out of the title, author line and footer, where Discord
    would print them as raw text (``ui.component_emoji.strip_custom_emoji``)."""
    embed = discord.Embed(
        title=strip_custom_emoji(spec.title),
        description=spec.description,
        url=spec.url,
        color=spec.color,
        timestamp=to_datetime(spec.timestamp) if spec.timestamp is not None else None,
    )
    author = strip_custom_emoji(spec.author_name)
    if author:
        embed.set_author(name=author, icon_url=spec.author_icon_url)
    for item in spec.fields:
        embed.add_field(name=item.name, value=item.value, inline=item.inline)
    footer = strip_custom_emoji(spec.footer_text)
    if footer:
        embed.set_footer(text=footer, icon_url=spec.footer_icon_url)
    if spec.thumbnail_url:
        embed.set_thumbnail(url=spec.thumbnail_url)
    if spec.image_url:
        embed.set_image(url=spec.image_url)
    return embed


def to_view(spec: MessageSpec) -> discord.ui.View | None:
    """Build a persistent view. Interactive items are wrapped in ``RoutedItem`` so clicks are routed by
    custom_id (restart-safe) and nothing is held in memory per message. A button or option label that starts with a
    custom emoji (an icon) shows it as the component's emoji (``ui.component_emoji``)."""
    if not spec.rows:
        return None
    from shopbot.ui.routing import RoutedItem  # local import: routing depends on this module

    view = discord.ui.View(timeout=None)
    for row_index, row in enumerate(spec.rows):
        for component in row:
            if isinstance(component, ButtonSpec):
                component = split_button(component)
                if component.style == "link":
                    view.add_item(
                        discord.ui.Button(
                            style=discord.ButtonStyle.link,
                            label=component.label,
                            url=component.url,
                            emoji=parse_emoji(component.emoji),
                            disabled=component.disabled,
                            row=row_index,
                        )
                    )
                    continue
                button: discord.ui.Button[Any] = discord.ui.Button(
                    style=_BUTTON_STYLES[component.style],
                    label=component.label,
                    custom_id=component.custom_id,
                    emoji=parse_emoji(component.emoji),
                    disabled=component.disabled,
                    row=row_index,
                )
                view.add_item(RoutedItem(button, row=row_index))
            elif isinstance(component, SelectSpec):
                select: discord.ui.Select[Any] = discord.ui.Select(
                    custom_id=component.custom_id,
                    placeholder=component.placeholder,
                    min_values=component.min_values,
                    max_values=component.max_values,
                    disabled=component.disabled,
                    row=row_index,
                    options=[
                        discord.SelectOption(
                            label=o.label,
                            value=o.value,
                            description=o.description,
                            emoji=parse_emoji(o.emoji),
                            default=o.default,
                        )
                        for o in map(split_option, component.options)
                    ],
                )
                view.add_item(RoutedItem(select, row=row_index))
    return view


def to_modal(spec: ModalSpec) -> discord.ui.Modal:
    modal = discord.ui.Modal(title=spec.title, custom_id=spec.custom_id, timeout=None)
    for item in spec.inputs:
        text_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            style=discord.TextStyle.paragraph if item.style == "paragraph" else discord.TextStyle.short,
            custom_id=item.custom_id,
            placeholder=item.placeholder,
            default=item.default,
            required=item.required,
            min_length=item.min_length,
            max_length=item.max_length,
        )
        modal.add_item(discord.ui.Label(text=item.label, description=item.description, component=text_input))
    return modal


def allowed_mentions(spec: MessageSpec) -> discord.AllowedMentions:
    return discord.AllowedMentions(
        everyone=False,
        users=[discord.Object(uid) for uid in spec.mention_user_ids] or False,
        roles=[discord.Object(rid) for rid in spec.mention_role_ids] or False,
        replied_user=False,
    )


def to_files(files: Sequence[FileSpec]) -> list[discord.File]:
    """Fresh ``discord.File`` objects (a File's stream cannot be reused after a send, e.g. a retried one).

    A file reference whose bytes were never loaded is refused (nothing is sent)."""
    missing = [Violation(f"files[{i}]", "file data is not loaded") for i, f in enumerate(files) if f.data is None]
    if missing:
        raise RenderError(missing)
    return [discord.File(io.BytesIO(f.data or b""), filename=f.filename) for f in files]


def send_kwargs(spec: MessageSpec) -> dict[str, Any]:
    """Keyword arguments for ``Messageable.send`` / ``InteractionResponse.send_message``.

    The banner (if any) is uploaded first and shown as the last embed's image (``place_banner``); when that embed
    shows its own picture, no embed uses the banner and Discord shows it above the embeds."""
    spec = place_banner(spec)
    check_message(spec)
    kwargs: dict[str, Any] = {
        "embeds": [to_embed(e) for e in spec.embeds],
        "allowed_mentions": allowed_mentions(spec),
    }
    view = to_view(spec)
    if view is not None:
        kwargs["view"] = view
    if spec.mention_text:
        kwargs["content"] = spec.mention_text
    files = message_files(spec)
    if files:
        kwargs["files"] = to_files(files)
    return kwargs


def edit_kwargs(spec: MessageSpec, current: Sequence[discord.Attachment] | None = None) -> dict[str, Any]:
    """Keyword arguments for message edits; components are replaced (or removed) to match the spec.

    ``current``: the message's attachments when known (a fetched message, the message of a clicked component), None
    when unknown. See ``edit_attachments`` for what happens to them. The banner is placed as in ``send_kwargs``."""
    spec = place_banner(spec)
    check_message(spec)
    kwargs: dict[str, Any] = {
        "embeds": [to_embed(e) for e in spec.embeds],
        "view": to_view(spec),
        "content": spec.mention_text,
        "allowed_mentions": allowed_mentions(spec),
    }
    attachments = edit_attachments(spec, current)
    if attachments is not None:
        kwargs["attachments"] = attachments
    return kwargs


def edit_attachments(
    spec: MessageSpec, current: Sequence[discord.Attachment] | None
) -> list[discord.Attachment | discord.File] | None:
    """The attachments an edit sends (Discord keeps exactly those), or None to leave the message's attachments alone.

    - The banner: an uploaded banner with the same name (same picture) is kept without uploading it again; a
      different or no banner replaces or removes earlier banners. While an embed shows the banner (``banner_inside``)
      the attachments are always sent, the kept banner included: Discord resolves an embed's ``attachment://`` image
      against the attachments an edit sends.
    - ``spec.files`` not None: exactly those files, uploaded again (``attachment://`` images stay attached).
      None: the message's other attachments are left as they are.
    - Unknown ``current``: the banner and files the spec wants are uploaded (when it wants any).
    """
    managed = spec.files is not None
    if current is None:
        if not managed and spec.banner is None:
            return None
        return list(to_files(message_files(spec)))
    banners = [a for a in current if is_banner_filename(a.filename)]
    kept = next((a for a in banners if spec.banner is not None and a.filename == spec.banner.filename), None)
    stale = any(a is not kept for a in banners)
    if not managed and not stale and not banner_inside(spec) and (spec.banner is None or kept is not None):
        return None  # nothing to change: the right banner (shown above the embeds) or none is already there
    result: list[discord.Attachment | discord.File] = []
    if spec.banner is not None:
        result.append(kept if kept is not None else to_files([spec.banner])[0])
    if managed:
        result += to_files(spec.files or ())
    else:
        result += [a for a in current if not is_banner_filename(a.filename)]
    return result


async def send_spec(channel: discord.abc.Messageable, spec: MessageSpec) -> discord.Message:
    return await channel.send(**send_kwargs(spec))


async def edit_spec(message: discord.Message | discord.PartialMessage, spec: MessageSpec) -> discord.Message:
    """Edit ``message`` to show ``spec``. A ``PartialMessage`` is fetched first (one request): its attachments decide
    whether the banner is kept, replaced or removed (raises ``discord.NotFound`` when the message is gone)."""
    if not isinstance(message, discord.Message):
        message = await message.fetch()
    return await message.edit(**edit_kwargs(spec, message.attachments))
