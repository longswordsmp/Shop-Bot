"""#how-to-buy: the step-by-step guide to the customer workflow, with link buttons to the relevant channels.

One clean embed: the configured title and introduction, then one field per numbered step (finding items, starting
a private ticket, completing the steps, checking the result - the owner writes them). Link buttons (one per
``how_to_buy.link_channels`` entry) open the configured channels. A guide too long for one message continues in
further messages (never cut); the link buttons are attached to the last one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import HowToBuySettings, Settings
from shopbot.features.guides import common
from shopbot.features.server_keys import PRIVATE_ACCESS, channel_placeholders, effective_access, reference_problems
from shopbot.ui.spec import ButtonSpec, EmbedField, MessageSpec, PreviewState, rows_of
from shopbot.ui.style import Ui

if TYPE_CHECKING:
    import discord

    from shopbot.context import App

HOW_TO_BUY = "how_to_buy"
PREFIX = "how_to_buy:"

TITLE = "how_to_buy.title"
INTRODUCTION = "how_to_buy.introduction"


def step_name_key(number: int) -> str:
    return f"how_to_buy.step.{number}.name"


def step_value_key(number: int) -> str:
    return f"how_to_buy.step.{number}.value"


def link_label_key(channel_key: str) -> str:
    return f"how_to_buy.link.{channel_key}.label"


def link_emoji_key(channel_key: str) -> str:
    return f"how_to_buy.link.{channel_key}.emoji"


# -- configuration -------------------------------------------------------------------------------------------


def content_keys(settings: Settings) -> list[ContentKey]:
    s = settings.how_to_buy
    links = channel_placeholders(settings)
    where = "the step-by-step guide posted in the how_to_buy channel"
    link_doc = (
        " You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links."
        if links
        else ""
    )
    group = "How to buy guide"
    keys = [
        ck(TITLE, Kind.TITLE, HOW_TO_BUY, group, f"Title of {where}."),
        ck(
            INTRODUCTION,
            Kind.DESCRIPTION,
            HOW_TO_BUY,
            group,
            f"Introduction at the top of {where}, above the steps.{link_doc}",
            placeholders=links,
        ),
    ]
    steps_group = "How to buy guide: steps"
    for number in range(1, s.steps + 1):
        keys += [
            ck(
                step_name_key(number),
                Kind.FIELD_NAME,
                HOW_TO_BUY,
                steps_group,
                f"Heading of step {number} of {s.steps} in {where} (write the step number yourself if you want "
                "one shown; channel links do not work in headings).",
            ),
            ck(
                step_value_key(number),
                Kind.FIELD_VALUE,
                HOW_TO_BUY,
                steps_group,
                f"Explanation of step {number} of {s.steps} in {where}.{link_doc}",
                placeholders=links,
            ),
        ]
    links_group = "How to buy guide: channel link buttons"
    for channel_key in s.link_channels:
        keys += [
            ck(
                link_label_key(channel_key),
                Kind.BUTTON_LABEL,
                HOW_TO_BUY,
                links_group,
                f"Label of the link button under {where} that opens the '{channel_key}' channel.",
            ),
            ck(
                link_emoji_key(channel_key),
                Kind.EMOJI,
                HOW_TO_BUY,
                links_group,
                f"Optional emoji on the link button that opens the '{channel_key}' channel.",
                required=False,
            ),
        ]
    return keys


def settings_problems(settings: Settings) -> list[str]:
    s = settings.how_to_buy
    problems: list[str] = []
    if not s.channel:
        problems.append("how_to_buy.channel: set the channel key (from server.channels) the guide is posted in")
    references = [("how_to_buy.channel", s.channel)]
    references += [("how_to_buy.link_channels", key) for key in s.link_channels]
    problems.extend(reference_problems(settings, channels=tuple(references)))
    for key in s.link_channels:
        # Customers cannot open staff/admin channels: a link button to one would not work for them.
        if effective_access(settings, key) in PRIVATE_ACCESS:
            problems.append(f"how_to_buy.link_channels: channel '{key}' is a staff/admin channel customers cannot open")
    return problems


def content_problems(settings: Settings, content: ContentStore) -> list[str]:
    return common.misplaced_link_problems(settings, content, content_keys(settings))


# -- views -----------------------------------------------------------------------------------------------------


def guide_messages(
    ui: Ui, s: HowToBuySettings, texts: common.Texts, link_url: Callable[[str], str]
) -> list[MessageSpec]:
    """The guide's messages. Raises ``MissingContent``/``ChannelNotCreated`` at runtime when text or a channel
    is missing (nothing incomplete is posted)."""
    steps = tuple(
        EmbedField(texts.required(step_name_key(n)), texts.required(step_value_key(n))) for n in range(1, s.steps + 1)
    )
    block = common.Block(
        tone="primary",
        title=texts.required(TITLE),
        description=texts.required(INTRODUCTION),
        fields=steps,
    )
    buttons = [
        ButtonSpec(
            label=texts.required(link_label_key(key)),
            style="link",
            url=link_url(key),
            emoji=texts.optional(link_emoji_key(key)),
        )
        for key in s.link_channels
    ]
    return common.guide_messages(ui, [block], rows_of(*buttons), banner="how-it-works")


# -- runtime ---------------------------------------------------------------------------------------------------


def _build(app: App, guild: discord.Guild) -> list[MessageSpec]:
    texts = common.runtime_texts(app, guild)
    return guide_messages(app.ui, app.settings.how_to_buy, texts, lambda key: common.live_channel_url(app, guild, key))


async def publish(app: App) -> list[str]:
    return await common.publish_guide(
        app,
        feature=HOW_TO_BUY,
        prefix=PREFIX,
        channel_key=app.settings.how_to_buy.channel,
        build=lambda guild: _build(app, guild),
    )


# -- preview ---------------------------------------------------------------------------------------------------

EXAMPLE_LINKS = tuple(f"example_{n}" for n in range(1, 26))


def _sample(key: str) -> str | None:
    if key.endswith(".value"):
        return common.long_sample(f"sample step text {key.split('.')[2]}", 900)
    if key.endswith(".emoji"):
        return None
    return f"[{key}]"


def preview(app: App) -> list[PreviewState]:
    s = app.settings.how_to_buy
    specs = guide_messages(app.ui, s, common.preview_texts(app), lambda key: common.preview_channel_url(app, key))
    states = common.preview_states(
        HOW_TO_BUY, "guide", "The step-by-step guide as posted in the how_to_buy channel by /setup.", specs
    )
    example = HowToBuySettings(channel="", steps=25, link_channels=EXAMPLE_LINKS)
    long_specs = guide_messages(
        app.ui, example, common.sample_texts(_sample), lambda key: f"https://example.com/channel/{key}"
    )
    states += common.preview_states(
        HOW_TO_BUY,
        "example_longest",
        "Synthetic example (not your configuration): the longest possible guide (25 long steps, 25 link "
        "buttons) continues in further messages instead of being cut; the buttons are under the last message.",
        long_specs,
    )
    return states
