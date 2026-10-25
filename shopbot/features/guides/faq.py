"""#faq: the questions and answers as one card.

An optional title and introduction, then one section per ``[[faq.sections]]`` entry: the section's heading in bold
(usually starting with one of the bot's icons), then its questions in bold, each followed by its answer. A FAQ
longer than one embed continues in the next embed and message; nothing is ever cut.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import FaqSection, FaqSettings, Settings
from shopbot.features.guides import common
from shopbot.features.server_keys import channel_placeholders, reference_problems
from shopbot.ui.card import PARAGRAPH, Card, Section, heading
from shopbot.ui.spec import MessageSpec, PreviewState
from shopbot.ui.style import Ui

if TYPE_CHECKING:
    import discord

    from shopbot.context import App

FAQ = "faq"
PREFIX = "faq:"

TITLE = "faq.title"
INTRODUCTION = "faq.introduction"


def section_title_key(section: str) -> str:
    return f"faq.section.{section}.title"


def question_key(section: str, number: int) -> str:
    return f"faq.section.{section}.q.{number}.question"


def answer_key(section: str, number: int) -> str:
    return f"faq.section.{section}.q.{number}.answer"


# -- configuration -------------------------------------------------------------------------------------------


def content_keys(settings: Settings) -> list[ContentKey]:
    links = channel_placeholders(settings)
    link_doc = (
        " You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links."
        if links
        else ""
    )
    where = "the FAQ posted in the faq channel"
    group = "FAQ header"
    keys = [
        ck(TITLE, Kind.TITLE, FAQ, group, f"Optional title at the top of {where}.", required=False),
        ck(
            INTRODUCTION,
            Kind.DESCRIPTION,
            FAQ,
            group,
            f"Optional introduction at the top of {where}, above the first section.{link_doc}",
            required=False,
            placeholders=links,
        ),
    ]
    for section in settings.faq.sections:
        sid = section.id
        section_group = f"FAQ section '{sid}'"
        keys.append(
            ck(
                section_title_key(sid),
                Kind.TITLE,
                FAQ,
                section_group,
                f"Heading of the '{sid}' section of {where}, shown in bold (e.g. the topic: ordering, payment...). "
                "It may start with one of the bot's icons, e.g. {icon_cart}.",
            )
        )
        for number in range(1, section.questions + 1):
            keys += [
                ck(
                    question_key(sid, number),
                    Kind.FIELD_NAME,
                    FAQ,
                    section_group,
                    f"Question {number} of {section.questions} in the '{sid}' section (channel links do not work "
                    "in questions).",
                ),
                ck(
                    answer_key(sid, number),
                    Kind.FIELD_VALUE,
                    FAQ,
                    section_group,
                    f"Answer to question {number} of {section.questions} in the '{sid}' section.{link_doc}",
                    placeholders=links,
                ),
            ]
    return keys


def settings_problems(settings: Settings) -> list[str]:
    s = settings.faq
    problems: list[str] = []
    if not s.channel:
        problems.append("faq.channel: set the channel key (from server.channels) the FAQ is posted in")
    if not s.sections:
        problems.append("faq.sections: add at least one [[faq.sections]] entry (id and number of questions)")
    problems.extend(reference_problems(settings, channels=(("faq.channel", s.channel),)))
    return problems


def content_problems(settings: Settings, content: ContentStore) -> list[str]:
    return common.misplaced_link_problems(settings, content, content_keys(settings))


# -- views -----------------------------------------------------------------------------------------------------


def faq_section(texts: common.Texts, section: FaqSection) -> Section:
    lines: list[str] = []
    for n in range(1, section.questions + 1):
        if lines:
            lines.append(PARAGRAPH)
        lines += [heading(texts.required(question_key(section.id, n))), texts.required(answer_key(section.id, n))]
    return Section(texts.required(section_title_key(section.id)), tuple(lines))


def faq_messages(ui: Ui, s: FaqSettings, texts: common.Texts) -> list[MessageSpec]:
    """The FAQ's messages. Raises ``MissingContent``/``ChannelNotCreated`` at runtime when text or a channel
    is missing (nothing incomplete is posted)."""
    card = Card(
        tone="info",
        title=texts.optional(TITLE),
        intro=texts.optional(INTRODUCTION),
        sections=tuple(faq_section(texts, section) for section in s.sections),
    )
    return common.guide_messages(ui, [card.block()], banner="faq")


# -- runtime ---------------------------------------------------------------------------------------------------


def _build(app: App, guild: discord.Guild) -> list[MessageSpec]:
    return faq_messages(app.ui, app.settings.faq, common.runtime_texts(app, guild))


async def publish(app: App) -> list[str]:
    return await common.publish_guide(
        app, feature=FAQ, prefix=PREFIX, channel_key=app.settings.faq.channel, build=lambda guild: _build(app, guild)
    )


# -- preview ---------------------------------------------------------------------------------------------------


def _sample_with(answer_length: int) -> Callable[[str], str | None]:
    def sample(key: str) -> str | None:
        if key.endswith(".answer"):
            return common.long_sample(f"sample answer {key.split('.')[2]}.{key.split('.')[4]}", answer_length)
        return f"[{key}]"

    return sample


EXAMPLE_MANY_SECTIONS = FaqSettings(sections=tuple(FaqSection(f"example_{n}", 6) for n in range(1, 15)))
EXAMPLE_LONG_SECTION = FaqSettings(sections=(FaqSection("example_long", 25),))


def preview(app: App) -> list[PreviewState]:
    specs = faq_messages(app.ui, app.settings.faq, common.preview_texts(app))
    states = common.preview_states(FAQ, "faq", "The FAQ as posted in the faq channel by /setup.", specs)
    states += common.preview_states(
        FAQ,
        "example_many_sections",
        "Synthetic example (not your configuration): 14 sections of 6 long answers do not fit one embed "
        "(4096 characters), so the FAQ continues in further embeds and messages.",
        faq_messages(app.ui, EXAMPLE_MANY_SECTIONS, common.sample_texts(_sample_with(250))),
    )
    states += common.preview_states(
        FAQ,
        "example_long_section",
        "Synthetic example (not your configuration): a section with 25 long answers does not fit one embed, so it "
        "continues in the next embeds and messages without being cut.",
        faq_messages(app.ui, EXAMPLE_LONG_SECTION, common.sample_texts(_sample_with(1000))),
    )
    return states
