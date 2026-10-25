"""#shipping: delivery/fulfillment information and the list of supported regions by availability status.

Everything is generated from configuration (no built-in claims): a header (title, introduction, and optionally
the information fields), then one card per enabled availability status (fully supported / partially supported /
unsupported) listing its regions, each line written with that status's indicator, then the information fields
when they are configured to come after the regions. Cards use the success / warning / danger colors so the
statuses are visually distinct. Long lists continue in further embeds and messages; nothing is cut.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import (
    SHIPPING_STATUSES,
    Settings,
    ShippingRegion,
    ShippingSettings,
)
from shopbot.features.guides import common
from shopbot.features.server_keys import channel_placeholders, reference_problems
from shopbot.ui.spec import EmbedField, MessageSpec, PreviewState
from shopbot.ui.style import Ui

if TYPE_CHECKING:
    import discord

    from shopbot.context import App

SHIPPING = "shipping"
PREFIX = "shipping:"

TITLE = "shipping.title"
INTRODUCTION = "shipping.introduction"
FORMAT_REGION = "shipping.format.region"
FORMAT_REGION_WITH_NOTE = "shipping.format.region_with_note"

# Card color of each availability status (colors come from settings.branding.colors).
STATUS_TONES = {"full": "success", "partial": "warning", "none": "danger"}
STATUS_MEANING = {"full": "fully supported", "partial": "partially supported", "none": "not supported"}


def status_label_key(status: str) -> str:
    return f"shipping.status.{status}.label"


def status_indicator_key(status: str) -> str:
    return f"shipping.status.{status}.indicator"


def status_description_key(status: str) -> str:
    return f"shipping.status.{status}.description"


def region_name_key(region: str) -> str:
    return f"shipping.region.{region}.name"


def region_note_key(region: str) -> str:
    return f"shipping.region.{region}.note"


def info_name_key(number: int) -> str:
    return f"shipping.info.{number}.name"


def info_value_key(number: int) -> str:
    return f"shipping.info.{number}.value"


# -- configuration -------------------------------------------------------------------------------------------


def content_keys(settings: Settings) -> list[ContentKey]:
    s = settings.shipping
    links = channel_placeholders(settings)
    link_doc = (
        " You can link channels with {channel_<key>} (e.g. {channel_faq}); they become clickable #channel links."
        if links
        else ""
    )
    where = "the shipping information posted in the shipping channel"
    group = "Shipping header"
    keys = [
        ck(TITLE, Kind.TITLE, SHIPPING, group, f"Title of {where}."),
        ck(
            INTRODUCTION,
            Kind.DESCRIPTION,
            SHIPPING,
            group,
            f"Introduction at the top of {where}.{link_doc}",
            placeholders=links,
        ),
    ]
    status_group = "Shipping availability statuses"
    for status in s.statuses:
        meaning = STATUS_MEANING[status]
        keys += [
            ck(
                status_label_key(status),
                Kind.TITLE,
                SHIPPING,
                status_group,
                f"Title of the card listing the regions that are '{status}' ({meaning}). {{indicator}} is replaced "
                f"with this status's indicator. Required while '{status}' is listed in shipping.statuses.",
                placeholders=("indicator",),
            ),
            ck(
                status_indicator_key(status),
                Kind.EMOJI,
                SHIPPING,
                status_group,
                f"Indicator (emoji) of the '{status}' status ({meaning}), written at the start of each of its regions "
                "(as {indicator} in shipping.format.region).",
            ),
            ck(
                status_description_key(status),
                Kind.DESCRIPTION,
                SHIPPING,
                status_group,
                f"Optional explanation at the top of the '{status}' card ({meaning}), above its regions. The card is "
                f"shown when it has regions or this explanation.{link_doc}",
                required=False,
                placeholders=links,
            ),
        ]
    line_group = "Shipping region lines"
    keys += [
        ck(
            FORMAT_REGION,
            Kind.FORMAT,
            SHIPPING,
            line_group,
            "How one region line is written, e.g. '{indicator} {name}'. {indicator} is the status indicator, {name} "
            "the region name. Must contain {indicator} and {name}. Required once regions are configured.",
            required=bool(s.regions),
            placeholders=("indicator", "name"),
        ),
        ck(
            FORMAT_REGION_WITH_NOTE,
            Kind.FORMAT,
            SHIPPING,
            line_group,
            "How a region line with a note is written, e.g. '{indicator} {name} ({note})'. Must contain "
            "{indicator}, {name} and {note}. Required once any region has a note.",
            required=False,
            placeholders=("indicator", "name", "note"),
        ),
    ]
    region_group = "Shipping regions"
    for region in s.regions:
        keys += [
            ck(
                region_name_key(region.id),
                Kind.FIELD_NAME,
                SHIPPING,
                region_group,
                f"Name of the region '{region.id}' (listed under the '{region.status}' status).",
            ),
            ck(
                region_note_key(region.id),
                Kind.FIELD_NAME,
                SHIPPING,
                region_group,
                f"Optional short note next to the region '{region.id}' (written with "
                f"shipping.format.region_with_note).{link_doc}",
                required=False,
                placeholders=links,
            ),
        ]
    info_group = "Shipping information"
    position = "above" if s.info_position == "before" else "below"
    for number in range(1, s.info_sections + 1):
        keys += [
            ck(
                info_name_key(number),
                Kind.FIELD_NAME,
                SHIPPING,
                info_group,
                f"Heading of information section {number} of {s.info_sections} (e.g. delivery or fulfillment "
                f"options), shown {position} the region list.",
            ),
            ck(
                info_value_key(number),
                Kind.FIELD_VALUE,
                SHIPPING,
                info_group,
                f"Text of information section {number} of {s.info_sections}.{link_doc}",
                placeholders=links,
            ),
        ]
    return keys


def settings_problems(settings: Settings) -> list[str]:
    s = settings.shipping
    problems: list[str] = []
    if not s.channel:
        problems.append("shipping.channel: set the channel key (from server.channels) the information is posted in")
    problems.extend(reference_problems(settings, channels=(("shipping.channel", s.channel),)))
    for index, region in enumerate(s.regions):
        if region.status and region.status not in s.statuses:
            problems.append(
                f"shipping.regions[{index}].status: '{region.status}' is not enabled in shipping.statuses "
                f"(enabled: {list(s.statuses)})"
            )
    return problems


def _missing_placeholders(text: str, names: tuple[str, ...]) -> list[str]:
    return [name for name in names if "{" + name + "}" not in text]


def content_problems(settings: Settings, content: ContentStore) -> list[str]:
    problems = common.misplaced_link_problems(settings, content, content_keys(settings))
    for key, names in (
        (FORMAT_REGION, ("indicator", "name")),
        (FORMAT_REGION_WITH_NOTE, ("indicator", "name", "note")),
    ):
        text = content.get(key)
        missing = _missing_placeholders(text, names) if text is not None else []
        if missing:
            problems.append(f"{key} must contain {', '.join('{' + n + '}' for n in missing)}")
    if not content.has(FORMAT_REGION_WITH_NOTE):
        with_note = [r.id for r in settings.shipping.regions if content.has(region_note_key(r.id))]
        if with_note:
            problems.append(f"configure {FORMAT_REGION_WITH_NOTE}: region '{with_note[0]}' has a note")
    return problems


# -- views -----------------------------------------------------------------------------------------------------


def region_line(texts: common.Texts, region: ShippingRegion, indicator: str) -> str:
    name = texts.required(region_name_key(region.id))
    note = texts.optional(region_note_key(region.id))
    if note is not None:
        return texts.required(FORMAT_REGION_WITH_NOTE, indicator=indicator, name=name, note=note)
    return texts.required(FORMAT_REGION, indicator=indicator, name=name)


def shipping_messages(ui: Ui, s: ShippingSettings, texts: common.Texts) -> list[MessageSpec]:
    """The shipping information's messages. Raises ``MissingContent``/``ChannelNotCreated`` at runtime when text
    or a channel is missing (nothing incomplete is posted)."""
    info = tuple(
        EmbedField(texts.required(info_name_key(n)), texts.required(info_value_key(n)))
        for n in range(1, s.info_sections + 1)
    )
    before = s.info_position == "before"
    blocks = [
        common.Block(
            tone="primary",
            title=texts.required(TITLE),
            description=texts.required(INTRODUCTION),
            fields=info if before else (),
        )
    ]
    for status in s.statuses:
        regions = [region for region in s.regions if region.status == status]
        description = texts.optional(status_description_key(status))
        if not regions and description is None:
            continue
        indicator = texts.required(status_indicator_key(status))
        blocks.append(
            common.Block(
                tone=STATUS_TONES[status],
                title=texts.required(status_label_key(status), indicator=indicator),
                description=description,
                lines=tuple(region_line(texts, region, indicator) for region in regions),
            )
        )
    if info and not before:
        blocks.append(common.Block(tone="info", fields=info))
    return common.guide_messages(ui, blocks, banner="shipping")


# -- runtime ---------------------------------------------------------------------------------------------------


def _build(app: App, guild: discord.Guild) -> list[MessageSpec]:
    return shipping_messages(app.ui, app.settings.shipping, common.runtime_texts(app, guild))


async def publish(app: App) -> list[str]:
    return await common.publish_guide(
        app,
        feature=SHIPPING,
        prefix=PREFIX,
        channel_key=app.settings.shipping.channel,
        build=lambda guild: _build(app, guild),
    )


# -- preview ---------------------------------------------------------------------------------------------------

EXAMPLE_REGIONS = ShippingSettings(
    statuses=SHIPPING_STATUSES,
    regions=tuple(ShippingRegion(f"example_{n}", SHIPPING_STATUSES[n % 3]) for n in range(1, 301)),
    info_sections=25,
    info_position="after",
)


def _sample(key: str) -> str | None:
    parts = key.split(".")
    if key == FORMAT_REGION:
        return "[sample format] {indicator} {name}"
    if key == FORMAT_REGION_WITH_NOTE:
        return "[sample format] {indicator} {name} {note}"
    if parts[1] == "status" and parts[-1] == "indicator":
        return f"[{parts[2]}]"
    if parts[1] == "status" and parts[-1] == "label":
        return f"[{key}] {{indicator}}"
    if parts[1] == "region" and parts[-1] == "name":
        return f"[sample region {parts[2]}]"
    if parts[1] == "region" and parts[-1] == "note":
        return f"[sample note {parts[2]}]" if int(parts[2].removeprefix("example_")) % 4 == 0 else None
    if parts[1] == "info" and parts[-1] == "value":
        return common.long_sample(f"sample information {parts[2]}", 600)
    return f"[{key}]"


def preview(app: App) -> list[PreviewState]:
    specs = shipping_messages(app.ui, app.settings.shipping, common.preview_texts(app))
    states = common.preview_states(
        SHIPPING, "shipping", "The shipping information as posted in the shipping channel by /setup.", specs
    )
    states += common.preview_states(
        SHIPPING,
        "example_regions",
        "Synthetic example (not your configuration): 300 regions in three statuses plus 25 information sections "
        "continue in further embeds and messages instead of being cut.",
        shipping_messages(app.ui, EXAMPLE_REGIONS, common.sample_texts(_sample)),
    )
    return states
