"""Preview: every ``/announce`` UI state, built by the runtime view functions from synthetic sample data.

Titles, messages, field values, ids and links exist only in the preview and are obviously fake (``[sample title]``).
When no ``[[announcements.fields]]`` are configured, example states show how three extra fields would look, with
``⟦announcements.field.<id>.name⟧`` markers where the configured headings would be.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.core.permissions import CHANNEL_SEND_PERMISSIONS
from shopbot.features.announcements import service, views
from shopbot.features.announcements.content_keys import ANNOUNCEMENTS, field_name_key
from shopbot.ui.limits import EMBED_TOTAL_CHARS
from shopbot.ui.spec import FileSpec, MessageSpec, ModalSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_FORM_ID = "0123456789abcdef01234567"
SAMPLE_GUILD_ID = 100_000_000_000_000_000
SAMPLE_CHANNEL_ID = 100_000_000_000_000_001
SAMPLE_MESSAGE_ID = 100_000_000_000_000_002
SAMPLE_ROLE_ID = 100_000_000_000_000_003
SAMPLE_TITLE = "[sample title]"
SAMPLE_MESSAGE = "[sample announcement message]"
SAMPLE_IMAGE = f"{service.IMAGE_BASENAME}.png"
# A tiny valid PNG (1x1 pixel) so the preview shows an attached image without any external file.
SAMPLE_FILE = FileSpec.of(
    SAMPLE_IMAGE,
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082"
    ),
    "image/png",
)
EXAMPLE_FIELDS = (
    views.FormInput("example_1", "", "short", False, 256),
    views.FormInput("example_2", "", "paragraph", True, 1024),
    views.FormInput("example_3", "", "short", False, 100),
)


def sample_value(number: int) -> str:
    return f"[sample field value {number}]"


def sample_url() -> str:
    return f"https://discord.com/channels/{SAMPLE_GUILD_ID}/{SAMPLE_CHANNEL_ID}/{SAMPLE_MESSAGE_ID}"


def preview(app: App) -> list[PreviewState]:
    ui, op = app.ui, app.op
    settings = app.settings.announcements
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec | ModalSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=ANNOUNCEMENTS, name=name, note=note, spec=spec))

    def reply(reply_value: views.Reply) -> MessageSpec:
        return views.reply_spec(ui, op, reply_value)

    def notice(text: str, tone: str) -> Callable[[], MessageSpec]:
        return lambda: reply(views.notice_reply(op, text, tone=tone))

    # -- the staff form ---------------------------------------------------------------------------------------
    inputs = service.form_inputs(app)
    add(
        "form",
        "Form opened by /announce (staff only): title, message and the configured extra fields.",
        lambda: views.announcement_form(op, token=SAMPLE_FORM_ID, fields=inputs),
    )
    defaults = {views.TITLE_INPUT: SAMPLE_TITLE, views.MESSAGE_INPUT: SAMPLE_MESSAGE}
    for number, item in enumerate(settings.fields, start=1):
        defaults[views.field_input_id(item.id)] = sample_value(number)
    add(
        "form_retry",
        "The same form reopened by 'edit and try again' after an error, prefilled with what was typed.",
        lambda: views.announcement_form(op, token=SAMPLE_FORM_ID, fields=inputs, defaults=defaults),
    )
    example_inputs = [
        views.FormInput(
            item.field_id,
            views.field_label(op, item.field_id, None),
            item.style,
            item.required,
            item.max_length,
        )
        for item in EXAMPLE_FIELDS
    ]
    if not settings.fields:
        add(
            "form_example_fields",
            "Example: the form with three extra fields (none are configured in [[announcements.fields]]).",
            lambda: views.announcement_form(op, token=SAMPLE_FORM_ID, fields=example_inputs),
        )

    # -- the announcement (customer-facing) --------------------------------------------------------------------
    values = {item.id: sample_value(number) for number, item in enumerate(settings.fields, start=1)}
    add(
        "announcement",
        "Announcement posted in the announcements channel: staff title and message, every configured field, an "
        "uploaded image (attached to the message) and a mentioned role.",
        lambda: views.announcement_message(
            ui,
            title=SAMPLE_TITLE,
            message=SAMPLE_MESSAGE,
            fields=service.field_entries(app, values),
            image=SAMPLE_FILE,
            mention_role_id=SAMPLE_ROLE_ID,
        ),
    )
    if not settings.fields:
        example_fields = [
            (f"⟦{field_name_key(item.field_id)}⟧", sample_value(number))
            for number, item in enumerate(EXAMPLE_FIELDS, start=1)
        ]
        add(
            "announcement_example_fields",
            "Example: an announcement with three extra fields (markers show where the configured headings go), an "
            "image and a mention.",
            lambda: views.announcement_message(
                ui,
                title=SAMPLE_TITLE,
                message=SAMPLE_MESSAGE,
                fields=example_fields,
                image=SAMPLE_FILE,
                mention_role_id=SAMPLE_ROLE_ID,
            ),
        )
    add(
        "announcement_plain",
        "Announcement with only a title and a message (no fields filled in, no image, nobody mentioned).",
        lambda: views.announcement_message(ui, title=SAMPLE_TITLE, message=SAMPLE_MESSAGE),
    )

    # -- private replies to the staff member -------------------------------------------------------------------
    add(
        "published",
        "Private confirmation after publishing (with a mention and an image), with a link to the announcement.",
        lambda: reply(
            views.published_reply(
                op, channel_id=SAMPLE_CHANNEL_ID, url=sample_url(), mention_role_id=SAMPLE_ROLE_ID, has_image=True
            )
        ),
    )
    add(
        "published_plain",
        "Private confirmation after publishing a plain announcement.",
        lambda: reply(
            views.published_reply(
                op, channel_id=SAMPLE_CHANNEL_ID, url=sample_url(), mention_role_id=None, has_image=False
            )
        ),
    )
    add(
        "already_published",
        "The same form submitted again after it was published (nothing is posted twice).",
        lambda: reply(views.already_published_reply(op, url=sample_url())),
    )
    channel = f"<#{SAMPLE_CHANNEL_ID}>"
    required_label = next((item.label for item in inputs if item.required), example_inputs[1].label)
    add(
        "error_input",
        "Form errors (nothing posted), with the button that reopens the prefilled form.",
        lambda: reply(
            views.error_reply(
                op,
                [
                    op("announcements.error.title_empty"),
                    op("announcements.error.field_required", field=required_label),
                ],
                retry_token=SAMPLE_FORM_ID,
            )
        ),
    )
    add(
        "error_too_long",
        "The announcement exceeds Discord's 6000-character total (nothing posted).",
        lambda: reply(
            views.error_reply(
                op,
                [op("announcements.error.total_too_long", length=EMBED_TOTAL_CHARS + 120, limit=EMBED_TOTAL_CHARS)],
                retry_token=SAMPLE_FORM_ID,
            )
        ),
    )
    add(
        "error_channel",
        "The announcements channel does not exist yet, or the bot lacks permissions there (nothing posted).",
        lambda: reply(
            views.error_reply(
                op,
                [
                    op("announcements.error.channel_missing", channel=settings.channel or "announcements"),
                    op(
                        "announcements.error.missing_permissions",
                        permissions=", ".join((*CHANNEL_SEND_PERMISSIONS, "attach_files")),
                        channel=channel,
                    ),
                ],
                retry_token=SAMPLE_FORM_ID,
            )
        ),
    )
    add(
        "error_discord",
        "Discord refused or failed the post, or the role cannot be mentioned (nothing posted).",
        lambda: reply(
            views.error_reply(
                op,
                [
                    op("announcements.error.forbidden", channel=channel),
                    op("announcements.error.send_failed", http_status=503, code=0),
                    op("announcements.error.mention_not_pingable", role=f"<@&{SAMPLE_ROLE_ID}>", channel=channel),
                ],
                retry_token=SAMPLE_FORM_ID,
            )
        ),
    )
    add(
        "error_options",
        "Problems with the command options; the form is not opened (nothing stored or posted).",
        lambda: reply(
            views.error_reply(
                op,
                [
                    op("announcements.error.image_type", filename="[sample file].pdf"),
                    op("announcements.error.image_too_large", size="25.0", limit="10.0"),
                    op("announcements.error.mention_not_allowed", role="[sample role]"),
                    op("announcements.error.image_disabled"),
                ],
            )
        ),
    )
    for name, note, line, tone in (
        ("notice_busy", "A second submission while the first is being posted.", "announcements.error.busy", "info"),
        ("notice_expired", "A form used after it expired.", "announcements.error.expired", "warning"),
        (
            "notice_interrupted",
            "A form whose posting was interrupted by a restart.",
            "announcements.error.interrupted",
            "warning",
        ),
        ("notice_unknown", "An unknown or deleted form.", "announcements.error.form_unknown", "warning"),
        ("notice_not_owner", "A form opened by another staff member.", "announcements.error.not_owner", "danger"),
    ):
        add(name, note, notice(op(line, hours=service.DRAFT_TTL_SECONDS // 3600, channel=channel), tone))
    return states
