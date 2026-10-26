"""Preview: every ``/announce`` UI state, built by the runtime view functions from synthetic sample data.

Titles, messages, field values, ids and links exist only in the preview and are obviously fake (``[sample title]``).
When no ``[[announcements.fields]]`` are configured, example states show how three extra fields would look, with
``⟦announcements.field.<id>.name⟧`` markers where the configured headings would be. Each configured preset shows its
private preview (the announcement copy, the card, the card when its coupon does not exist) and its posted
announcement (with the claim button, and after the offer ended), with its configured texts and a sample coupon.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.config.settings import AnnouncementPreset
from shopbot.core.permissions import CHANNEL_SEND_PERMISSIONS
from shopbot.features.announcements import presets, service, views
from shopbot.features.announcements.content_keys import ANNOUNCEMENTS, field_name_key
from shopbot.features.checkout import discount_views
from shopbot.features.checkout import discounts as d
from shopbot.features.server_keys import channel_mentions, channel_placeholders
from shopbot.ui.limits import EMBED_TOTAL_CHARS
from shopbot.ui.spec import FileSpec, MessageSpec, ModalSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_FORM_ID = "0123456789abcdef01234567"
SAMPLE_GUILD_ID = 100_000_000_000_000_000
SAMPLE_CHANNEL_ID = 100_000_000_000_000_001
SAMPLE_MESSAGE_ID = 100_000_000_000_000_002
SAMPLE_ROLE_ID = 100_000_000_000_000_003
SAMPLE_LINK_CHANNEL_ID = 100_000_000_000_000_004
SAMPLE_CODE_ID = 7
SAMPLE_TIME = 1_767_225_600  # 2026-01-01 00:00 UTC
SAMPLE_PRODUCTS = ("[sample product 1]", "[sample product 2]")
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


def sample_code(preset: AnnouncementPreset) -> d.Code:
    """A buy one, get one free code with the preset's coupon name, limited to two sample products."""
    return d.Code(
        id=SAMPLE_CODE_ID,
        guild_id=SAMPLE_GUILD_ID,
        code=preset.coupon,
        kind=d.BOGO,
        value=0,
        min_subtotal_minor=0,
        max_uses=None,
        once_per_customer=False,
        expires_at=None,
        created_by=None,
        created_at=SAMPLE_TIME,
        ended_at=None,
        ended_by=None,
        product_ids=frozenset({1, 2}),
    )


def preview_links(app: App) -> dict[str, str]:
    """Every ``{channel_<key>}`` link: the real channel once /setup created it, a sample channel otherwise."""
    known = channel_mentions(app)
    return {name: known.get(name, f"<#{SAMPLE_LINK_CHANNEL_ID}>") for name in channel_placeholders(app.settings)}


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

    def notice(build_notice: Callable[[], views.Reply]) -> Callable[[], MessageSpec]:
        return lambda: reply(build_notice())

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
                    op(
                        "announcements.error.image_too_large",
                        size="25.0",
                        limit=service.megabytes(service.upload_limit(app)),
                    ),
                    op("announcements.error.mention_not_allowed", role="[sample role]"),
                    op("announcements.error.image_disabled"),
                ],
            )
        ),
    )
    notices: tuple[tuple[str, str, Callable[[], views.Reply]], ...] = (
        ("notice_busy", "A second submission while the first is being posted.", lambda: views.busy_reply(op)),
        (
            "notice_expired",
            "A form used after it expired.",
            lambda: views.expired_reply(op, hours=service.DRAFT_TTL_SECONDS // 3600),
        ),
        (
            "notice_interrupted",
            "A form whose posting was interrupted by a restart.",
            lambda: views.interrupted_reply(op, channel=channel),
        ),
        ("notice_unknown", "An unknown or deleted form.", lambda: views.unknown_form_reply(op)),
        ("notice_not_owner", "A form opened by another staff member.", lambda: views.not_owner_reply(op)),
    )
    for name, note, build_notice in notices:
        add(name, note, notice(build_notice))
    add_preset_states(app, add)
    return states


AddState = Callable[[str, str, Callable[[], MessageSpec | ModalSpec]], None]


def add_preset_states(app: App, add: AddState) -> None:
    """The states of every configured preset, then the replies all presets share."""
    ui, op = app.ui, app.op
    channel = f"<#{SAMPLE_CHANNEL_ID}>"

    def reply(value: views.Reply) -> MessageSpec:
        return views.reply_spec(ui, op, value)

    for preset in app.settings.announcements.presets:
        add_preset(app, add, preset)

    if not app.settings.announcements.presets:
        return
    sample = app.settings.announcements.presets[0]
    sample_coupon = sample.coupon or "[SAMPLE]"
    add(
        "preset_published",
        "Staff only: the preview card after Post announcement, with a link to the post and the coupon of its claim "
        "button.",
        lambda: reply(
            views.published_reply(
                op,
                channel_id=SAMPLE_CHANNEL_ID,
                url=sample_url(),
                mention_role_id=SAMPLE_ROLE_ID,
                has_image=False,
                coupon=views.coupon_line(op, sample_coupon, op("checkout.discounts.terms.bogo")),
            )
        ),
    )
    add(
        "preset_post_error",
        "Staff only: Post announcement failed because of the server (nothing posted); Post announcement stays for "
        "after the fix.",
        lambda: reply(
            views.preset_error_reply(
                op,
                [
                    op(
                        "announcements.error.missing_permissions",
                        permissions=", ".join((*CHANNEL_SEND_PERMISSIONS, "attach_files")),
                        channel=channel,
                    )
                ],
                token=SAMPLE_FORM_ID,
                preset_id=sample.id,
            )
        ),
    )
    add(
        "preset_already_announced",
        "Staff only: Post announcement (or the preview) when this preset already announced the same coupon, with a "
        "link to that announcement.",
        lambda: reply(
            views.preset_error_reply(
                op,
                [op("announcements.preset.already_announced", code=sample_coupon)],
                token=None,
                preset_id=sample.id,
                link_url=sample_url(),
            )
        ),
    )
    add(
        "preset_coupon_changed",
        "Staff only: Post announcement after the coupon was ended and created again since the preview (its terms may "
        "differ): nothing is posted, staff run the preset again.",
        lambda: reply(
            views.preset_error_reply(
                op, [op("announcements.preset.coupon_changed", code=sample_coupon)], token=None, preset_id=sample.id
            )
        ),
    )
    add(
        "preset_published_offer_ended",
        "Staff only: the card after Post announcement when the coupon ended while the announcement was being posted "
        "(its button is switched to the ended label right after).",
        lambda: reply(
            views.published_reply(
                op,
                channel_id=SAMPLE_CHANNEL_ID,
                url=sample_url(),
                mention_role_id=None,
                has_image=False,
                coupon=op("announcements.preset.ended_while_posting", code=sample_coupon),
            )
        ),
    )
    add(
        "preset_cancelled",
        "Staff only: the preview card after Cancel (nothing posted).",
        lambda: reply(views.cancelled_reply(op)),
    )
    add(
        "preset_notice_unknown",
        "Staff only: Post or Cancel on a preview that was cancelled or has expired.",
        lambda: reply(service.unknown_preview_reply(app)),
    )


def add_preset(app: App, add: AddState, preset: AnnouncementPreset) -> None:
    """One preset: its private preview (the copy, the card, the card while its coupon does not exist) and its posted
    announcement (with the claim button, then after the offer ended)."""
    ui, op = app.ui, app.op
    links = preview_links(app)
    channel = f"<#{SAMPLE_CHANNEL_ID}>"
    code = sample_code(preset) if preset.coupon else None
    coupon: tuple[str, str] | None = None
    if code is not None:
        money = lambda minor: ui.money(minor) or str(minor)  # noqa: E731
        coupon = (code.code, discount_views.terms_text(op, money, code, SAMPLE_PRODUCTS, now=SAMPLE_TIME))
    facts = views.preset_facts(op, channel=channel, mention_role_id=SAMPLE_ROLE_ID, has_image=False, coupon=coupon)
    prefix = f"preset_{preset.id}"

    def announcement(*, preview: bool, ended: bool = False) -> MessageSpec:
        return views.preset_announcement(
            ui,
            preset,
            links=links,
            code_id=code.id if code is not None else None,
            preview=preview,
            ended=ended,
            mention_role_id=None if preview else SAMPLE_ROLE_ID,
        )

    def reply(value: views.Reply) -> MessageSpec:
        return views.reply_spec(ui, op, value)

    add(
        f"{prefix}_preview_announcement",
        f"Staff only (/announce preset:{preset.id}): the private copy of the announcement as it will be posted (the "
        "mention is listed on the card), its claim button disabled. The preview card follows it.",
        lambda: announcement(preview=True),
    )
    add(
        f"{prefix}_preview",
        f"Staff only (/announce preset:{preset.id}): the private card under the copy, with the channel, the mention, "
        "the coupon's terms in plain words, Post announcement and Cancel.",
        lambda: reply(views.preset_preview_reply(op, name=preset.name, facts=facts, token=SAMPLE_FORM_ID)),
    )
    if preset.coupon:
        missing = op(
            "announcements.preset.coupon_missing", code=preset.coupon, command=presets.create_command(app, preset)
        )
        problem_facts = views.preset_facts(
            op, channel=channel, mention_role_id=SAMPLE_ROLE_ID, has_image=False, coupon=None
        )
        add(
            f"{prefix}_preview_problem",
            f"Staff only (/announce preset:{preset.id}) while its coupon {preset.coupon} does not exist: the fix (the "
            "/discount create command) and no Post button; nothing is stored.",
            lambda: reply(
                views.preset_problem_reply(
                    op, name=preset.name, preset_id=preset.id, facts=problem_facts, problems=[missing]
                )
            ),
        )
    with_button = ", the claim button of its coupon" if preset.coupon else ""
    add(
        f"{prefix}_announcement",
        f"The '{preset.id}' preset announcement as posted in the announcements channel (a mentioned role"
        f"{with_button}).",
        lambda: announcement(preview=False),
    )
    if preset.coupon:
        add(
            f"{prefix}_announcement_ended",
            f"The posted '{preset.id}' announcement once its coupon {preset.coupon} ended or expired: the claim button "
            "is replaced by the disabled ended label.",
            lambda: announcement(preview=False, ended=True),
        )
