"""Pure builders for every message and form of the ticket system.

These functions take the ``Ui`` (content + branding), settings, and plain data, and return
``MessageSpec``/``ModalSpec`` objects. They never call Discord, so runtime code, the offline preview,
and the limits checker all render exactly the same specs. Missing required content raises
``MissingContent``; optional elements whose content is missing are omitted.

User-supplied data (display names, form answers, close reasons) is escaped with
``discord.utils.escape_markdown``. Mentions inside embeds never ping; pings are only added through
``MessageSpec.mention_user_ids``/``mention_role_ids``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from discord.utils import escape_markdown

from shopbot.config.content import MissingContent
from shopbot.config.settings import TicketCategory, TicketSettings
from shopbot.features import core_keys as core
from shopbot.features.tickets import content_keys as k
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_FIELDS, EMBED_TOTAL_CHARS
from shopbot.ui.spec import (
    ButtonSpec,
    ButtonStyle,
    Component,
    EmbedField,
    EmbedSpec,
    InputStyle,
    MessageSpec,
    ModalSpec,
    SelectOptionSpec,
    SelectSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui

Rows = tuple[tuple[Component, ...], ...]
Values = dict[str, str | int]

_BUTTON_STYLES: dict[str, ButtonStyle] = {
    "primary": "primary",
    "secondary": "secondary",
    "success": "success",
    "danger": "danger",
}
_INPUT_STYLES: dict[str, InputStyle] = {"short": "short", "paragraph": "paragraph"}

# Raw characters of a form answer per embed field. Escaping at most doubles the length, so every field
# value stays within Discord's 1024-character field limit without cutting any character.
ANSWER_CHUNK = 500


# -- small helpers ------------------------------------------------------------------------------------


def number_text(settings: TicketSettings, number: int) -> str:
    """The ticket number as shown everywhere ({number}), zero-padded to ``number_padding``."""
    return str(number).zfill(settings.number_padding) if settings.number_padding else str(number)


def user_mention(user_id: int) -> str:
    return f"<@{user_id}>"


def channel_mention(channel_id: int) -> str:
    return f"<#{channel_id}>"


def escape(text: str) -> str:
    return escape_markdown(text)


def _text(ui: Ui, key: str, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def _embed(
    ui: Ui,
    title_key: str | None,
    description_key: str | None,
    *,
    required: Sequence[str],
    values: Values | None = None,
    **kwargs: object,
) -> EmbedSpec:
    for key in required:
        if not ui.content.has(key):
            raise MissingContent(key)
    embed = ui.content_embed(title_key, description_key, values=values, require=required, **kwargs)
    if embed is None:
        raise MissingContent(description_key or title_key or "")
    return embed


def _button(ui: Ui, key: str, custom_id: str, *, style: ButtonStyle, emoji_key: str | None = None) -> ButtonSpec:
    if not ui.content.has(key):
        raise MissingContent(key)
    button = ui.button(key, custom_id, style=style, emoji_key=emoji_key)
    if button is None:
        raise MissingContent(key)
    return button


def _split(text: str, size: int) -> list[str]:
    """Split ``text`` into pieces of at most ``size`` characters, preferring line boundaries.
    Whitespace-only pieces are dropped (an embed field cannot be blank)."""
    pieces: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            pieces.append(current)
            current = ""
        current += line
    if current:
        pieces.append(current)
    return [p.strip("\n") for p in pieces if p.strip()]


def _field_size(item: EmbedField) -> int:
    return len(item.name) + len(item.value)


def _pack_fields(first: EmbedSpec, fields: Sequence[EmbedField], overflow_base: EmbedSpec) -> list[EmbedSpec]:
    """Put as many fields as fit into ``first``; the rest go into extra embeds built from
    ``overflow_base`` (each sent as its own message, so every message stays within Discord's limits)."""
    used = first.text_length()
    taken: list[EmbedField] = list(first.fields)
    index = 0
    while index < len(fields) and len(taken) < EMBED_FIELDS and used + _field_size(fields[index]) <= EMBED_TOTAL_CHARS:
        taken.append(fields[index])
        used += _field_size(fields[index])
        index += 1
    embeds = [replace(first, fields=tuple(taken))]
    base_used = overflow_base.text_length()
    current: list[EmbedField] = []
    current_used = base_used
    for item in fields[index:]:
        if current and (len(current) == EMBED_FIELDS or current_used + _field_size(item) > EMBED_TOTAL_CHARS):
            embeds.append(replace(overflow_base, fields=tuple(current)))
            current, current_used = [], base_used
        current.append(item)
        current_used += _field_size(item)
    if current:
        embeds.append(replace(overflow_base, fields=tuple(current)))
    return embeds


# -- panel and form -------------------------------------------------------------------------------------


def panel(ui: Ui, settings: TicketSettings, *, style: str | None = None) -> MessageSpec:
    """The public ticket panel: one button per category, or one selection menu (``panel_style``)."""
    embed = _embed(ui, k.PANEL_TITLE, k.PANEL_DESCRIPTION, required=(k.PANEL_TITLE, k.PANEL_DESCRIPTION), panel=True)
    style = style or settings.panel_style
    if style == "select":
        options: list[SelectOptionSpec] = []
        for category in settings.categories:
            options.append(
                SelectOptionSpec(
                    label=_text(ui, k.category_key(category.id, "button_label")),
                    value=category.id,
                    description=ui.content.get(k.category_key(category.id, "select_description")),
                    emoji=ui.content.get(k.category_key(category.id, "button_emoji")),
                )
            )
        select = SelectSpec(
            custom_id=cid(k.ROUTE, "pick"),
            options=tuple(options),
            placeholder=ui.content.get(k.PANEL_SELECT_PLACEHOLDER),
        )
        return MessageSpec(embeds=(embed,), rows=rows_of(select))
    buttons = [
        _button(
            ui,
            k.category_key(category.id, "button_label"),
            cid(k.ROUTE, "open", category.id),
            style=_BUTTON_STYLES.get(category.button_style, "primary"),
            emoji_key=k.category_key(category.id, "button_emoji"),
        )
        for category in settings.categories
    ]
    return MessageSpec(embeds=(embed,), rows=rows_of(*buttons))


def form_modal(ui: Ui, category: TicketCategory) -> ModalSpec:
    """The form shown before a ticket of ``category`` is created. Input custom_ids are the field ids."""
    inputs = tuple(
        TextInputSpec(
            custom_id=field.id,
            label=_text(ui, k.form_field_key(category.id, field.id, "label")),
            style=_INPUT_STYLES.get(field.style, "short"),
            description=ui.content.get(k.form_field_key(category.id, field.id, "description")),
            placeholder=ui.content.get(k.form_field_key(category.id, field.id, "placeholder")),
            required=field.required,
            min_length=field.min_length or None,
            max_length=field.max_length,
        )
        for field in category.form
    )
    return ModalSpec(
        custom_id=cid(k.ROUTE, "form", category.id),
        title=_text(ui, k.category_key(category.id, "form_title")),
        inputs=inputs,
    )


# -- ticket channel ---------------------------------------------------------------------------------------


def control_rows(ui: Ui, ticket_id: int, *, assigned: bool) -> Rows:
    """Buttons on the welcome message of an open ticket: close + claim (or unclaim when claimed)."""
    close = _button(ui, k.BUTTON_CLOSE, cid(k.ROUTE, "close", ticket_id), style="danger")
    if assigned:
        claim = _button(ui, k.BUTTON_UNCLAIM, cid(k.ROUTE, "unclaim", ticket_id), style="secondary")
    else:
        claim = _button(ui, k.BUTTON_CLAIM, cid(k.ROUTE, "claim", ticket_id), style="success")
    return rows_of(close, claim)


def answer_fields(ui: Ui, category: TicketCategory, answers: Mapping[str, str]) -> list[EmbedField]:
    """Form answers as data fields named by the form labels; empty optional answers are skipped."""
    fields: list[EmbedField] = []
    for field in category.form:
        answer = (answers.get(field.id) or "").strip()
        if not answer:
            continue
        label = _text(ui, k.form_field_key(category.id, field.id, "label"))
        for piece in _split(answer, ANSWER_CHUNK):
            fields.append(EmbedField(name=label, value=escape(piece)))
    return fields


def welcome_messages(
    ui: Ui,
    settings: TicketSettings,
    category: TicketCategory,
    *,
    ticket_id: int,
    number: int,
    user_id: int,
    user_name: str,
    answers: Mapping[str, str],
    assigned: bool = False,
    ping_role_ids: tuple[int, ...] = (),
) -> list[MessageSpec]:
    """Welcome message (with the controls; pings the customer) followed by overflow messages that only
    exist when the form answers do not fit into one message."""
    values: Values = {
        "user_mention": user_mention(user_id),
        "user_name": escape(user_name),
        "number": number_text(settings, number),
    }
    title_key = k.category_key(category.id, "welcome_title")
    description_key = k.category_key(category.id, "welcome_description")
    base = _embed(ui, title_key, description_key, required=(title_key, description_key), values=values, timestamp=True)
    embeds = _pack_fields(base, answer_fields(ui, category, answers), ui.embed())
    first = MessageSpec(
        embeds=(embeds[0],),
        rows=control_rows(ui, ticket_id, assigned=assigned),
        mention_user_ids=(user_id,),
        mention_role_ids=ping_role_ids,
    )
    return [first, *(MessageSpec(embeds=(embed,)) for embed in embeds[1:])]


def channel_topic(
    ui: Ui, settings: TicketSettings, category: TicketCategory, *, number: int, user_name: str
) -> str | None:
    """Optional channel topic (None when not configured)."""
    return ui.text(
        k.category_key(category.id, "channel_topic"),
        number=number_text(settings, number),
        user_name=escape(user_name),
    )


def closed_message(
    ui: Ui,
    settings: TicketSettings,
    *,
    ticket_id: int,
    number: int,
    closer_id: int | None,
    reason: str | None = None,
    transcripts: bool,
) -> MessageSpec:
    """Posted when a ticket closes. ``closer_id=None`` means it was closed automatically (inactivity)."""
    values: Values = {"number": number_text(settings, number)}
    if closer_id is None:
        embed = _embed(
            ui,
            k.CLOSED_TITLE,
            k.AUTO_CLOSED,
            required=(k.CLOSED_TITLE, k.AUTO_CLOSED),
            values=values,
            tone="neutral",
            timestamp=True,
        )
    else:
        values["closer_mention"] = user_mention(closer_id)
        embed = _embed(
            ui,
            k.CLOSED_TITLE,
            k.CLOSED_DESCRIPTION,
            required=(k.CLOSED_TITLE, k.CLOSED_DESCRIPTION),
            values=values,
            tone="neutral",
            timestamp=True,
        )
        reason_field = ui.field(k.CLOSED_REASON_LABEL, escape(reason) if reason else None)
        if reason_field is not None:
            embed = replace(embed, fields=(*embed.fields, reason_field))
    buttons: list[ButtonSpec] = []
    if settings.allow_reopen:
        buttons.append(_button(ui, k.BUTTON_REOPEN, cid(k.ROUTE, "reopen", ticket_id), style="success"))
    if transcripts:
        buttons.append(_button(ui, k.BUTTON_TRANSCRIPT, cid(k.ROUTE, "transcript", ticket_id), style="secondary"))
    buttons.append(_button(ui, k.BUTTON_DELETE, cid(k.ROUTE, "delete", ticket_id), style="danger"))
    return MessageSpec(embeds=(embed,), rows=rows_of(*buttons))


def _status(
    ui: Ui, key: str, *, tone: str, values: Values | None = None, mention_user_ids: tuple[int, ...] = ()
) -> MessageSpec:
    embed = _embed(ui, None, key, required=(key,), values=values, tone=tone)
    return MessageSpec(embeds=(embed,), mention_user_ids=mention_user_ids)


def claimed(ui: Ui, staff_id: int) -> MessageSpec:
    return _status(ui, k.CLAIMED, tone="info", values={"staff_mention": user_mention(staff_id)})


def unclaimed(ui: Ui) -> MessageSpec:
    return _status(ui, k.UNCLAIMED, tone="info")


def reopened(ui: Ui, actor_id: int) -> MessageSpec:
    return _status(ui, k.REOPENED, tone="success", values={"actor_mention": user_mention(actor_id)})


def deletion_scheduled(ui: Ui, minutes: int) -> MessageSpec:
    return _status(ui, k.DELETION_SCHEDULED, tone="warning", values={"minutes": minutes})


def auto_close_warning(ui: Ui, *, minutes: int, user_id: int) -> MessageSpec:
    return _status(
        ui,
        k.AUTO_CLOSE_WARNING,
        tone="warning",
        values={"minutes": minutes, "user_mention": user_mention(user_id)},
        mention_user_ids=(user_id,),
    )


def user_added(ui: Ui, user_id: int) -> MessageSpec:
    return _status(
        ui, k.USER_ADDED, tone="info", values={"user_mention": user_mention(user_id)}, mention_user_ids=(user_id,)
    )


def user_removed(ui: Ui, user_id: int) -> MessageSpec:
    return _status(ui, k.USER_REMOVED, tone="info", values={"user_mention": user_mention(user_id)})


# -- private prompts and replies ------------------------------------------------------------------------------


def _confirm(ui: Ui, title_key: str, description_key: str, confirm_id: str, cancel_id: str) -> MessageSpec:
    embed = _embed(ui, title_key, description_key, required=(description_key,), tone="warning")
    confirm = _button(ui, core.BUTTON_CONFIRM, confirm_id, style="danger")
    cancel = _button(ui, core.BUTTON_CANCEL, cancel_id, style="secondary")
    return MessageSpec(embeds=(embed,), rows=rows_of(confirm, cancel))


def close_confirm(ui: Ui, ticket_id: int) -> MessageSpec:
    return _confirm(
        ui,
        k.CLOSE_CONFIRM_TITLE,
        k.CLOSE_CONFIRM_DESCRIPTION,
        cid(k.ROUTE, "closeok", ticket_id),
        cid(k.ROUTE, "closeno", ticket_id),
    )


def delete_confirm(ui: Ui, ticket_id: int) -> MessageSpec:
    return _confirm(
        ui,
        k.DELETE_CONFIRM_TITLE,
        k.DELETE_CONFIRM_DESCRIPTION,
        cid(k.ROUTE, "delok", ticket_id),
        cid(k.ROUTE, "delno", ticket_id),
    )


def resolved_prompt(spec: MessageSpec) -> MessageSpec:
    """A confirmation prompt after it was answered: same text, buttons removed."""
    return replace(spec, rows=())


def created_reply(ui: Ui, settings: TicketSettings, *, channel_id: int, number: int) -> MessageSpec:
    return _status(
        ui,
        k.CREATED,
        tone="success",
        values={"channel_mention": channel_mention(channel_id), "number": number_text(settings, number)},
    )


def error_reply(ui: Ui, key: str, **values: str | int) -> MessageSpec:
    """Private error message exactly as the router sends it (used by the preview)."""
    embed = ui.error_embed(key, **values)
    if embed is None:
        raise MissingContent(key)
    return MessageSpec(embeds=(embed,))


# -- staff alert and transcripts --------------------------------------------------------------------------------


def staff_alert(
    ui: Ui,
    settings: TicketSettings,
    category: TicketCategory,
    *,
    number: int,
    user_id: int,
    channel_id: int,
    ping_role_ids: tuple[int, ...] = (),
) -> MessageSpec:
    values: Values = {
        "user_mention": user_mention(user_id),
        "channel_mention": channel_mention(channel_id),
        "number": number_text(settings, number),
        "category_label": _text(ui, k.category_key(category.id, "button_label")),
    }
    embed = _embed(
        ui,
        k.STAFF_ALERT_TITLE,
        k.STAFF_ALERT_DESCRIPTION,
        required=(k.STAFF_ALERT_TITLE, k.STAFF_ALERT_DESCRIPTION),
        values=values,
        tone="primary",
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), mention_role_ids=ping_role_ids)


def transcript_post(ui: Ui, settings: TicketSettings, *, number: int, user_id: int, closer_id: int) -> MessageSpec:
    values: Values = {
        "number": number_text(settings, number),
        "user_mention": user_mention(user_id),
        "closer_mention": user_mention(closer_id),
    }
    embed = _embed(
        ui,
        k.TRANSCRIPT_TITLE,
        k.TRANSCRIPT_DESCRIPTION,
        required=(k.TRANSCRIPT_TITLE, k.TRANSCRIPT_DESCRIPTION),
        values=values,
        tone="neutral",
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,))


def transcript_dm(ui: Ui, settings: TicketSettings, *, number: int) -> MessageSpec:
    values: Values = {"number": number_text(settings, number)}
    embed = _embed(
        ui,
        k.TRANSCRIPT_DM_TITLE,
        k.TRANSCRIPT_DM_DESCRIPTION,
        required=(k.TRANSCRIPT_DM_TITLE, k.TRANSCRIPT_DM_DESCRIPTION),
        values=values,
        tone="neutral",
    )
    return MessageSpec(embeds=(embed,))
