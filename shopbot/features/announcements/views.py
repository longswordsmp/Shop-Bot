"""Pure view builders of ``/announce`` (used at runtime and by the preview).

- ``announcement_form``: the staff-only form (operator text labels). Its custom id carries only the form's token.
- ``announcement_message``: the customer-facing post in the announcements channel. Title, message and field values
  are typed by staff; field headings come from content keys; footer/author/branding imagery from the shared branding
  configuration. An uploaded image is referenced as ``attachment://<filename>`` so it stays attached to the message.
- ``reply_spec`` and the ``*_reply`` helpers: the private operator replies (confirmation and errors).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_TITLE, INPUT_LABEL, INPUT_VALUE
from shopbot.ui.spec import (
    ButtonSpec,
    EmbedField,
    InputStyle,
    MessageSpec,
    ModalSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui

OperatorText = Callable[..., str]

ROUTE = "an"
SUBMIT_ACTION = "submit"
RETRY_ACTION = "retry"
TITLE_INPUT = "title"
MESSAGE_INPUT = "message"
TITLE_MAX = EMBED_TITLE
MESSAGE_MAX = INPUT_VALUE  # a form input holds at most 4000 characters (an embed description 4096)


def field_input_id(field_id: str) -> str:
    return f"field_{field_id}"


def submit_custom_id(token: str) -> str:
    return cid(ROUTE, SUBMIT_ACTION, token)


def retry_custom_id(token: str) -> str:
    return cid(ROUTE, RETRY_ACTION, token)


def attachment_url(filename: str) -> str:
    return f"attachment://{filename}"


# -- the staff form ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FormInput:
    """A configured extra field as shown in the form."""

    field_id: str
    label: str
    style: InputStyle = "short"
    required: bool = False
    max_length: int = 1024


def field_label(op: OperatorText, field_id: str, name: str | None) -> str:
    """The configured field heading when it fits a form label (45 characters), else an operator label."""
    if name and name.strip() and len(name) <= INPUT_LABEL:
        return name
    return op("announcements.form.field_label", field_id=field_id)


def _default(defaults: Mapping[str, str], input_id: str, max_length: int) -> str | None:
    """A prefilled value (retry after an error); left out when it no longer fits the input."""
    value = defaults.get(input_id)
    if not value or len(value) > max_length:
        return None
    return value


def announcement_form(
    op: OperatorText, *, token: str, fields: Sequence[FormInput], defaults: Mapping[str, str] | None = None
) -> ModalSpec:
    values = defaults or {}
    inputs = [
        TextInputSpec(
            custom_id=TITLE_INPUT,
            label=op("announcements.form.title_label"),
            style="short",
            description=op("announcements.form.title_description"),
            default=_default(values, TITLE_INPUT, TITLE_MAX),
            required=True,
            max_length=TITLE_MAX,
        ),
        TextInputSpec(
            custom_id=MESSAGE_INPUT,
            label=op("announcements.form.message_label"),
            style="paragraph",
            description=op("announcements.form.message_description"),
            default=_default(values, MESSAGE_INPUT, MESSAGE_MAX),
            required=True,
            max_length=MESSAGE_MAX,
        ),
    ]
    for item in fields:
        input_id = field_input_id(item.field_id)
        inputs.append(
            TextInputSpec(
                custom_id=input_id,
                label=item.label,
                style=item.style,
                description=op(
                    "announcements.form.field_required" if item.required else "announcements.form.field_optional"
                ),
                default=_default(values, input_id, item.max_length),
                required=item.required,
                max_length=item.max_length,
            )
        )
    return ModalSpec(custom_id=submit_custom_id(token), title=op("announcements.form.title"), inputs=tuple(inputs))


# -- the announcement (customer-facing) -------------------------------------------------------------------------


def announcement_message(
    ui: Ui,
    *,
    title: str,
    message: str,
    fields: Sequence[tuple[str, str]] = (),
    image_filename: str | None = None,
    mention_role_id: int | None = None,
) -> MessageSpec:
    """The post in the announcements channel. ``fields`` are (configured heading, staff value) pairs; empty values
    are left out. Nothing is mentioned unless ``mention_role_id`` is given (then only that role)."""
    embed = ui.embed(
        title=title,
        description=message,
        tone="primary",
        fields=tuple(EmbedField(name=name, value=value) for name, value in fields if value),
        image_url=attachment_url(image_filename) if image_filename else None,
        panel=True,
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), mention_role_ids=(mention_role_id,) if mention_role_id else ())


# -- operator replies -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    """A private operator reply. ``retry_token`` adds the "edit and try again" button (reopens the prefilled form)."""

    title: str
    lines: tuple[str, ...] = ()
    tone: str = "info"
    retry_token: str | None = None
    link_url: str | None = None


def reply_spec(ui: Ui, op: OperatorText, reply: Reply) -> MessageSpec:
    embed = ui.operator_embed(reply.title, "\n".join(reply.lines) or None, tone=reply.tone)
    link = ButtonSpec(label=op("announcements.done.open"), style="link", url=reply.link_url) if reply.link_url else None
    retry = (
        ButtonSpec(
            label=op("announcements.button.retry"), custom_id=retry_custom_id(reply.retry_token), style="primary"
        )
        if reply.retry_token
        else None
    )
    return MessageSpec(embeds=(embed,), rows=rows_of(link, retry))


def published_reply(
    op: OperatorText, *, channel_id: int, url: str, mention_role_id: int | None, has_image: bool
) -> Reply:
    lines = [op("announcements.done.description", channel=f"<#{channel_id}>", url=url)]
    if mention_role_id:
        lines.append(op("announcements.done.mention", role=f"<@&{mention_role_id}>"))
    if has_image:
        lines.append(op("announcements.done.image"))
    return Reply(op("announcements.done.title"), tuple(lines), tone="success", link_url=url)


def already_published_reply(op: OperatorText, *, url: str) -> Reply:
    return Reply(op("announcements.done.title"), (op("announcements.done.already", url=url),), link_url=url)


def error_reply(op: OperatorText, problems: Sequence[str], *, retry_token: str | None = None) -> Reply:
    """Nothing was posted. With ``retry_token`` the reply offers to reopen the form with the text already typed."""
    hint = op("announcements.error.retry_hint" if retry_token else "announcements.error.rerun_hint")
    return Reply(op("announcements.error.title"), (*problems, "", hint), tone="danger", retry_token=retry_token or None)


def notice_reply(op: OperatorText, line: str, *, tone: str = "warning") -> Reply:
    """A reply about the form itself (in progress, expired, unknown...)."""
    return Reply(op("announcements.notice.title"), (line,), tone=tone)
