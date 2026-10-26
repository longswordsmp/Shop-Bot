"""Pure view builders of ``/announce`` (used at runtime and by the preview).

- ``announcement_form``: the staff-only form (operator text labels). Its custom id carries only the form's token.
- ``announcement_message``: the customer-facing post in the announcements channel. Title, message and field values
  are typed by staff; field headings come from content keys; footer/author/branding imagery from the shared branding
  configuration. The announcements banner picture is uploaded first (it closes the card, or shows above it when the
  announcement has an image); an uploaded image is uploaded after it and referenced as ``attachment://<filename>``
  so it stays attached to the message.
- ``reply_spec`` and the ``*_reply`` helpers: the private operator replies (confirmation and errors), each with the
  banner of its outcome.
- Presets (``/announce preset:<id>``): the same post built from the preset's configured texts (``{excluded_areas}``
  filled from the shipping settings), with the claim button of its coupon (``claim_button``; disabled in the private
  preview) or, once the offer ended, the disabled "offer ended" button (``ended_button``); the private preview card
  (``preset_preview_reply``: channel, mention, the coupon's terms, Post announcement and Cancel) and its problem,
  error and cancelled variants.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import AnnouncementPreset
from shopbot.features.announcements.content_keys import (
    COUPON_PLACEHOLDER,
    EXCLUDED_AREAS_PLACEHOLDER,
    PRESET_CLAIM_BUTTON,
    PRESET_DESCRIPTION,
    PRESET_ENDED_BUTTON,
    PRESET_TITLE,
    preset_key,
)
from shopbot.features.checkout import discount_views
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_TITLE, INPUT_LABEL, INPUT_VALUE
from shopbot.ui.spec import (
    ButtonSpec,
    Component,
    EmbedField,
    FileSpec,
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
POST_ACTION = "post"
CANCEL_ACTION = "cancel"
TITLE_INPUT = "title"
MESSAGE_INPUT = "message"
TITLE_MAX = EMBED_TITLE
MESSAGE_MAX = INPUT_VALUE  # a form input holds at most 4000 characters (an embed description 4096)
ANNOUNCEMENT_BANNER: BannerId = "announcements"


def field_input_id(field_id: str) -> str:
    return f"field_{field_id}"


def submit_custom_id(token: str) -> str:
    return cid(ROUTE, SUBMIT_ACTION, token)


def retry_custom_id(token: str) -> str:
    return cid(ROUTE, RETRY_ACTION, token)


def post_custom_id(token: str) -> str:
    return cid(ROUTE, POST_ACTION, token)


def cancel_custom_id(token: str) -> str:
    return cid(ROUTE, CANCEL_ACTION, token)


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
    image: FileSpec | None = None,
    mention_role_id: int | None = None,
    rows: tuple[tuple[Component, ...], ...] = (),
    banner: BannerId = ANNOUNCEMENT_BANNER,
    image_url: str | None = None,
) -> MessageSpec:
    """The post in the announcements channel. ``fields`` are (configured heading, staff value) pairs; empty values
    are left out. Nothing is mentioned unless ``mention_role_id`` is given (then only that role). ``rows``: a
    preset's claim button; ``banner``: a preset's banner picture; ``image_url``: the uploaded picture shown from its
    link (a preset's private preview, before it is attached)."""
    embed = ui.embed(
        title=title,
        description=message,
        tone="primary",
        fields=tuple(EmbedField(name=name, value=value) for name, value in fields if value),
        image_url=attachment_url(image.filename) if image is not None else image_url,
        panel=True,
        timestamp=True,
    )
    return MessageSpec(
        embeds=(embed,),
        rows=rows,
        mention_role_ids=(mention_role_id,) if mention_role_id else (),
        files=(image,) if image is not None else None,
        banner=ui.banner(banner),
    )


def claim_button(ui: Ui, label_key: str, code_id: int, *, preview: bool = False) -> ButtonSpec:
    """A preset's claim button (``checkout.discount_views.claim_button``: ``sb:dc:claim:<code_id>``, handled by the
    checkout feature); disabled in the private preview. Raises ``MissingContent`` when its label is not set."""
    button = discount_views.claim_button(ui, label_key, code_id)
    return replace(button, disabled=True) if preview else button


def ended_button(ui: Ui, label_key: str, code_id: int) -> ButtonSpec | None:
    """The disabled button that replaces the claim button once the offer ended (None when its label is not set)."""
    return ui.button(label_key, discount_views.claim_id(code_id), style="secondary", disabled=True)


def _required(ui: Ui, key: str, **values: str | int) -> str:
    text = ui.text(key, **values)
    if not text:
        raise MissingContent(key)
    return text


def preset_announcement(
    ui: Ui,
    preset: AnnouncementPreset,
    *,
    links: Mapping[str, str],
    code_id: int | None,
    excluded_areas: str = "",
    preview: bool = False,
    ended: bool = False,
    image: FileSpec | None = None,
    image_url: str | None = None,
    mention_role_id: int | None = None,
) -> MessageSpec:
    """A preset's announcement: its configured title and text (``links``: the ``{channel_<key>}`` values; ``{coupon}``:
    the coupon's code; ``{excluded_areas}``: ``excluded_areas``, e.g. "Alaska and North Dakota"), its banner and, when
    it has a coupon, the claim button of the code ``code_id`` (disabled when ``preview``; the disabled "offer ended"
    button when ``ended``). Raises ``MissingContent`` when a text it needs is not set."""
    title = _required(ui, preset_key(preset.id, PRESET_TITLE), **{EXCLUDED_AREAS_PLACEHOLDER: excluded_areas})
    values: dict[str, str | int] = {**links, EXCLUDED_AREAS_PLACEHOLDER: excluded_areas}
    if preset.coupon:
        values[COUPON_PLACEHOLDER] = preset.coupon
    description = _required(ui, preset_key(preset.id, PRESET_DESCRIPTION), **values)
    rows: tuple[tuple[Component, ...], ...] = ()
    if preset.coupon:
        if code_id is None and not preview:
            raise ValueError("a posted preset with a coupon needs its code")
        code = code_id or 0  # the preview of a coupon that does not exist yet: the button is disabled anyway
        if ended:
            key = preset_key(preset.id, PRESET_ENDED_BUTTON)
            button = ended_button(ui, key, code)
            if button is None:
                raise MissingContent(key)
        else:
            button = claim_button(ui, preset_key(preset.id, PRESET_CLAIM_BUTTON), code, preview=preview)
        rows = ((button,),)
    return announcement_message(
        ui,
        title=title,
        message=description,
        image=image,
        image_url=image_url,
        mention_role_id=mention_role_id,
        rows=rows,
        banner=preset.banner,
    )


# -- operator replies -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    """A private operator reply. ``retry_token`` adds the "edit and try again" button (reopens the prefilled form);
    ``banner`` is the picture closing it."""

    title: str
    lines: tuple[str, ...] = ()
    tone: str = "info"
    retry_token: str | None = None
    link_url: str | None = None
    banner: BannerId = "general-response"
    post_token: str | None = None  # a preset's preview: "Post announcement"
    cancel_token: str | None = None  # a preset's preview: "Cancel"


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
    post = (
        ButtonSpec(label=op("announcements.preset.post"), custom_id=post_custom_id(reply.post_token), style="primary")
        if reply.post_token
        else None
    )
    cancel = (
        ButtonSpec(
            label=op("announcements.preset.cancel"), custom_id=cancel_custom_id(reply.cancel_token), style="secondary"
        )
        if reply.cancel_token
        else None
    )
    return MessageSpec(embeds=(embed,), rows=rows_of(link, retry, post, cancel), banner=ui.banner(reply.banner))


def published_reply(
    op: OperatorText,
    *,
    channel_id: int,
    url: str,
    mention_role_id: int | None,
    has_image: bool,
    coupon: str | None = None,
) -> Reply:
    """``coupon``: a preset's line about its claim button (``coupon_line``)."""
    lines = [op("announcements.done.description", channel=f"<#{channel_id}>", url=url)]
    if mention_role_id:
        lines.append(op("announcements.done.mention", role=f"<@&{mention_role_id}>"))
    if has_image:
        lines.append(op("announcements.done.image"))
    if coupon:
        lines.append(coupon)
    return Reply(
        op("announcements.done.title"), tuple(lines), tone="success", link_url=url, banner="successful-confirmation"
    )


def already_published_reply(op: OperatorText, *, url: str) -> Reply:
    return Reply(
        op("announcements.done.title"),
        (op("announcements.done.already", url=url),),
        link_url=url,
        banner="general-response",
    )


def error_reply(op: OperatorText, problems: Sequence[str], *, retry_token: str | None = None) -> Reply:
    """Nothing was posted. With ``retry_token`` the reply offers to reopen the form with the text already typed."""
    hint = op("announcements.error.retry_hint" if retry_token else "announcements.error.rerun_hint")
    return Reply(
        op("announcements.error.title"),
        (*problems, "", hint),
        tone="danger",
        retry_token=retry_token or None,
        banner="error",
    )


def notice_reply(op: OperatorText, line: str, *, banner: BannerId, tone: str = "warning") -> Reply:
    """A reply about the form itself (in progress, expired, unknown...); ``banner`` fits the situation."""
    return Reply(op("announcements.notice.title"), (line,), tone=tone, banner=banner)


def busy_reply(op: OperatorText) -> Reply:
    """The same form submitted again while the first submission is still being posted."""
    return notice_reply(op, op("announcements.error.busy"), banner="submission-and-processing", tone="info")


def expired_reply(op: OperatorText, *, hours: int) -> Reply:
    """A form used after its lifetime: it is void (run /announce again)."""
    return notice_reply(op, op("announcements.error.expired", hours=hours), banner="cancellation")


def interrupted_reply(op: OperatorText, *, channel: str) -> Reply:
    """A form whose posting was interrupted by a restart (it may or may not have been posted)."""
    return notice_reply(op, op("announcements.error.interrupted", channel=channel), banner="error")


def unknown_form_reply(op: OperatorText) -> Reply:
    """An unknown, deleted or malformed form."""
    return notice_reply(op, op("announcements.error.form_unknown"), banner="error")


def not_owner_reply(op: OperatorText) -> Reply:
    """A form opened by another staff member."""
    return notice_reply(op, op("announcements.error.not_owner"), banner="error", tone="danger")


# -- presets (operator replies) -----------------------------------------------------------------------------------


def coupon_line(op: OperatorText, code: str, terms: str) -> str:
    """The coupon of a preset's claim button and its terms in plain words (``checkout.discount_codes.terms_text``)."""
    return op("announcements.preset.done_coupon", code=code, terms=terms)


def preset_facts(
    op: OperatorText, *, channel: str, mention_role_id: int | None, has_image: bool, coupon: tuple[str, str] | None
) -> list[str]:
    """The preview's facts: channel, mention, image, and the coupon (code, terms) when the preset has one."""
    lines = [op("announcements.preset.channel", channel=channel)]
    if mention_role_id:
        lines.append(op("announcements.preset.mention", role=f"<@&{mention_role_id}>"))
    else:
        lines.append(op("announcements.preset.mention_none"))
    if has_image:
        lines.append(op("announcements.preset.image"))
    if coupon is not None:
        lines.append(op("announcements.preset.coupon", code=coupon[0], terms=coupon[1]))
    return lines


def preset_preview_reply(
    op: OperatorText, *, name: str, facts: Sequence[str], token: str, has_button: bool = True
) -> Reply:
    """The private card under a preset's preview: what will be posted where, with Post announcement and Cancel.
    ``has_button``: the announcement has a claim button (a preset with a coupon), which the introduction mentions."""
    intro = "announcements.preset.preview_intro" if has_button else "announcements.preset.preview_intro_plain"
    return Reply(
        op("announcements.preset.preview_title", name=name),
        (op(intro), "", *facts),
        tone="info",
        banner="general-response",
        post_token=token,
        cancel_token=token,
    )


def preset_problem_reply(
    op: OperatorText,
    *,
    name: str,
    preset_id: str,
    facts: Sequence[str],
    problems: Sequence[str],
    link_url: str | None = None,
) -> Reply:
    """A preset that cannot be posted now (e.g. its coupon does not exist): the problems and how to fix them, no Post
    button (nothing is stored). ``link_url``: the announcement a problem is about (Open announcement)."""
    return Reply(
        op("announcements.preset.problem_title", name=name),
        (*facts, "", *problems, "", op("announcements.preset.problem_hint", preset=preset_id)),
        tone="danger",
        banner="error",
        link_url=link_url,
    )


def preset_error_reply(
    op: OperatorText, problems: Sequence[str], *, token: str | None, preset_id: str, link_url: str | None = None
) -> Reply:
    """Post announcement failed (nothing posted). With ``token`` the card keeps Post announcement (after fixing the
    problem, e.g. a permission, staff press it again) and Cancel; without, staff run the preset again. ``link_url``:
    the announcement a problem is about (Open announcement)."""
    hint = (
        op("announcements.preset.post_retry_hint")
        if token
        else op("announcements.preset.post_rerun_hint", preset=preset_id)
    )
    return Reply(
        op("announcements.error.title"),
        (*problems, "", hint),
        tone="danger",
        banner="error",
        post_token=token,
        cancel_token=token,
        link_url=link_url,
    )


def cancelled_reply(op: OperatorText) -> Reply:
    """A preset's preview after Cancel."""
    return Reply(
        op("announcements.preset.cancelled_title"),
        (op("announcements.preset.cancelled"),),
        tone="neutral",
        banner="cancellation",
    )


def preset_notice_reply(op: OperatorText, line: str, *, banner: BannerId, tone: str = "warning") -> Reply:
    """A reply about a preset's preview itself (in progress, expired, unknown...)."""
    return Reply(op("announcements.preset.notice_title"), (line,), tone=tone, banner=banner)


def feature_disabled_reply(op: OperatorText, *, feature: str) -> Reply:
    """/announce while the feature is disabled or not fully configured."""
    return Reply(op("command.feature_disabled", feature=feature), tone="warning", banner="error")
