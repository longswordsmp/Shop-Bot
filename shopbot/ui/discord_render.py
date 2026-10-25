"""Convert UI specs into discord.py objects and send/edit them safely.

Every outgoing message is validated against Discord limits first; a spec that violates a limit raises
``RenderError`` and nothing is sent (text is never truncated). Mentions are disabled by default and
enabled only for the user/role IDs a spec explicitly lists.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from shopbot.core.clock import to_datetime
from shopbot.logging_setup import log_event
from shopbot.ui.limits import Violation, validate_message, validate_modal
from shopbot.ui.spec import ButtonSpec, EmbedSpec, MessageSpec, ModalSpec, SelectSpec

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
    embed = discord.Embed(
        title=spec.title,
        description=spec.description,
        url=spec.url,
        color=spec.color,
        timestamp=to_datetime(spec.timestamp) if spec.timestamp is not None else None,
    )
    if spec.author_name:
        embed.set_author(name=spec.author_name, icon_url=spec.author_icon_url)
    for item in spec.fields:
        embed.add_field(name=item.name, value=item.value, inline=item.inline)
    if spec.footer_text:
        embed.set_footer(text=spec.footer_text, icon_url=spec.footer_icon_url)
    if spec.thumbnail_url:
        embed.set_thumbnail(url=spec.thumbnail_url)
    if spec.image_url:
        embed.set_image(url=spec.image_url)
    return embed


def to_view(spec: MessageSpec) -> discord.ui.View | None:
    """Build a persistent view. Interactive items are wrapped in ``RoutedItem`` so clicks are routed by
    custom_id (restart-safe) and nothing is held in memory per message."""
    if not spec.rows:
        return None
    from shopbot.ui.routing import RoutedItem  # local import: routing depends on this module

    view = discord.ui.View(timeout=None)
    for row_index, row in enumerate(spec.rows):
        for component in row:
            if isinstance(component, ButtonSpec):
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
                        for o in component.options
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


def send_kwargs(spec: MessageSpec) -> dict[str, Any]:
    """Keyword arguments for ``Messageable.send`` / ``InteractionResponse.send_message``."""
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
    return kwargs


def edit_kwargs(spec: MessageSpec) -> dict[str, Any]:
    """Keyword arguments for message edits; components are replaced (or removed) to match the spec."""
    check_message(spec)
    return {
        "embeds": [to_embed(e) for e in spec.embeds],
        "view": to_view(spec),
        "content": spec.mention_text,
        "allowed_mentions": allowed_mentions(spec),
    }


async def send_spec(channel: discord.abc.Messageable, spec: MessageSpec) -> discord.Message:
    return await channel.send(**send_kwargs(spec))


async def edit_spec(message: discord.Message | discord.PartialMessage, spec: MessageSpec) -> discord.Message:
    return await message.edit(**edit_kwargs(spec))
