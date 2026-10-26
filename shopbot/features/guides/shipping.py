"""#shipping: where we ship and what delivery costs, as one card in one message.

Everything comes from configuration: the title and introduction, an optional legend, then one section per
``[[shipping.groups]]`` (e.g. North America, Europe) listing its countries after a status dot (the bot's icons):
available everywhere, available with exceptions (``[shipping.excluded_areas]``, e.g. "United States (except Alaska
and North Dakota)"), then the countries listed as not available. Below come the delivery options with their prices
(``[[checkout.fulfillment_options]]``) and the information sections (processing, tracking).

The country in ``shipping.list_areas`` (the US) closes the same card with a section like a group's: its heading, then
every area by name (``LISTED_AREAS``: the 50 states and DC, written "Washington, D.C.": ``area_display_name``),
alphabetically, on one line per status like the countries: the available ones after the blue dot, the country's
``excluded_areas`` after the grey one. The card has no fields. With the shipped configuration it fits one embed
(Discord allows 4096 characters of text and 6000 per embed); a card longer than that (far more countries or
information than shipped) continues in the next embed and message, and nothing is cut.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import (
    CheckoutSettings,
    ExtraDef,
    FulfillmentOption,
    Settings,
    ShippingGroup,
    ShippingSettings,
)
from shopbot.core.countries import LISTED_AREAS, area_display_name
from shopbot.features.checkout.content_keys import extra_key, option_key
from shopbot.features.guides import common
from shopbot.features.server_keys import channel_placeholders, reference_problems
from shopbot.ui.card import Card, Section
from shopbot.ui.spec import MessageSpec, PreviewState
from shopbot.ui.style import Ui

if TYPE_CHECKING:
    import discord

    from shopbot.context import App

SHIPPING = "shipping"
PREFIX = "shipping:"

TITLE = "shipping.title"
INTRODUCTION = "shipping.introduction"
LEGEND = "shipping.legend"
NOT_AVAILABLE_HEADING = "shipping.not_available.heading"
NOT_AVAILABLE_OTHERS = "shipping.not_available.others"
FORMAT_EXCLUDED = "shipping.format.excluded"
FORMAT_LAST_AREA = "shipping.format.last_area"
DELIVERY_HEADING = "shipping.delivery.heading"
EXTRAS_HEADING = "shipping.extras.heading"
FORMAT_DELIVERY = "shipping.format.delivery"
AREAS_HEADING = "shipping.areas.heading"
AREAS_INTRODUCTION = "shipping.areas.introduction"

# A country's availability: everywhere, with excluded areas, not at all.
STATUSES = ("full", "partial", "none")
STATUS_MEANING = {
    "full": "we ship everywhere in the country",
    "partial": "we ship to the country except some areas",
    "none": "we do not ship to the country",
}
COUNTRY_SEPARATOR = " · "
NO_BREAK_SPACE = "\u00a0"


def status_indicator_key(status: str) -> str:
    return f"shipping.status.{status}.indicator"


def group_name_key(group: str) -> str:
    return f"shipping.group.{group}.name"


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
    where = "the shipping card posted in the shipping channel"
    icon_doc = " It may start with one of the bot's icons, e.g. {icon_truck}."
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
        ck(
            LEGEND,
            Kind.DESCRIPTION,
            SHIPPING,
            group,
            "Optional lines under the introduction explaining the status dots, best one per line so a dot stays "
            "with its words on a phone, e.g. '{icon_dot_blue} We ship everywhere', '{icon_dot_yellow} Some areas "
            "excluded' and '{icon_dot_grey} Not available'.",
            required=False,
        ),
    ]
    status_group = "Shipping status dots"
    for status in STATUSES:
        keys.append(
            ck(
                status_indicator_key(status),
                Kind.FIELD_NAME,
                SHIPPING,
                status_group,
                f"Dot shown before the countries where {STATUS_MEANING[status]}, e.g. {{icon_dot_blue}}. Required "
                f"while such countries are listed.",
                required=_status_used(s, status),
            )
        )
    country_group = "Shipping countries"
    for g in s.groups:
        keys.append(
            ck(
                group_name_key(g.id),
                Kind.FIELD_NAME,
                SHIPPING,
                country_group,
                f"Heading of the country group '{g.id}' ({', '.join(g.countries[:6])}"
                f"{', ...' if len(g.countries) > 6 else ''}), shown in bold above its countries.",
            )
        )
    has_excluded = bool(s.excluded_areas)
    keys += [
        ck(
            FORMAT_EXCLUDED,
            Kind.FORMAT,
            SHIPPING,
            country_group,
            "How a country with excluded areas is written, e.g. '{country} (except {areas})'. Must contain "
            "{country} and {areas}.",
            required=has_excluded,
            placeholders=("country", "areas"),
        ),
        ck(
            FORMAT_LAST_AREA,
            Kind.FORMAT,
            SHIPPING,
            country_group,
            "How the last of several excluded areas is joined, e.g. '{areas} and {last}' (gives 'Alaska and North "
            "Dakota'; other areas are separated by commas). Must contain {areas} and {last}.",
            required=any(len(areas) > 1 for areas in s.excluded_areas.values()),
            placeholders=("areas", "last"),
        ),
        ck(
            NOT_AVAILABLE_HEADING,
            Kind.FIELD_NAME,
            SHIPPING,
            country_group,
            "Heading of the countries we do not ship to (shipping.not_available), e.g. 'Not available'. Required "
            "when countries are listed there or shipping.not_available.others is set.",
            required=bool(s.not_available),
        ),
        ck(
            NOT_AVAILABLE_OTHERS,
            Kind.FIELD_VALUE,
            SHIPPING,
            country_group,
            "Optional last item of the not-available list, e.g. 'every country not listed above'.",
            required=False,
        ),
    ]
    options = settings.checkout.fulfillment_options
    delivery_group = "Shipping delivery options"
    keys += [
        ck(
            DELIVERY_HEADING,
            Kind.FIELD_NAME,
            SHIPPING,
            delivery_group,
            f"Heading of the delivery options and their prices (checkout.fulfillment_options).{icon_doc}",
            required=bool(options),
        ),
        ck(
            EXTRAS_HEADING,
            Kind.FIELD_NAME,
            SHIPPING,
            delivery_group,
            f"Heading of the optional extras and their prices (checkout.extras, written with shipping.format.delivery)."
            f"{icon_doc}",
            required=bool(settings.checkout.extras),
        ),
        ck(
            FORMAT_DELIVERY,
            Kind.FORMAT,
            SHIPPING,
            delivery_group,
            "How one delivery option or extra is written, e.g. '**{label}** · {price}'. {label} is its name "
            "(checkout.fulfillment.<id>.label or checkout.extra.<id>.label), {price} its price, {description} its "
            "description (empty when it has none).",
            required=bool(options) or bool(settings.checkout.extras),
            placeholders=("label", "price", "description"),
        ),
    ]
    if s.list_areas:
        areas_group = "Shipping areas one by one"
        keys += [
            ck(
                AREAS_HEADING,
                Kind.FIELD_NAME,
                SHIPPING,
                areas_group,
                "Heading of the list of every area of a country one by one (shipping.list_areas, e.g. every US "
                "state) at the bottom of the shipping card, shown in bold, e.g. '{icon_map} US STATES'. {country} "
                "is the country's name.",
                placeholders=("country",),
            ),
            ck(
                AREAS_INTRODUCTION,
                Kind.FIELD_VALUE,
                SHIPPING,
                areas_group,
                "Optional line under that heading, e.g. 'Find your state before you order.' {country} is the "
                "country's name. The areas follow on one line per status, like the countries: the available ones "
                "after shipping.status.full.indicator, the excluded ones after shipping.status.none.indicator (the "
                "legend at the top of the card explains the dots).",
                required=False,
                placeholders=("country",),
            ),
        ]
    info_group = "Shipping information"
    for number in range(1, s.info_sections + 1):
        keys += [
            ck(
                info_name_key(number),
                Kind.FIELD_NAME,
                SHIPPING,
                info_group,
                f"Heading of information section {number} of {s.info_sections} (e.g. processing times or "
                f"tracking), shown in bold below the delivery options.{icon_doc}",
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


def _status_used(s: ShippingSettings, status: str) -> bool:
    if status == "none":
        return bool(s.not_available) or any(s.excluded_areas.get(code) for code in s.list_areas)
    partial = [code for code in s.countries if code in s.excluded_areas]
    return bool(partial) if status == "partial" else len(partial) < len(s.countries)


def settings_problems(settings: Settings) -> list[str]:
    s = settings.shipping
    problems: list[str] = []
    if not s.channel:
        problems.append("shipping.channel: set the channel key (from server.channels) the card is posted in")
    if not s.groups:
        problems.append("shipping.groups: add at least one [[shipping.groups]] entry (id and countries we ship to)")
    problems.extend(reference_problems(settings, channels=(("shipping.channel", s.channel),)))
    return problems


def _missing_placeholders(text: str, names: tuple[str, ...]) -> list[str]:
    return [name for name in names if "{" + name + "}" not in text]


def content_problems(settings: Settings, content: ContentStore) -> list[str]:
    problems = common.misplaced_link_problems(settings, content, content_keys(settings))
    for key, names in ((FORMAT_EXCLUDED, ("country", "areas")), (FORMAT_LAST_AREA, ("areas", "last"))):
        text = content.get(key)
        missing = _missing_placeholders(text, names) if text is not None else []
        if missing:
            problems.append(f"{key} must contain {', '.join('{' + n + '}' for n in missing)}")
    return problems


# -- views -----------------------------------------------------------------------------------------------------


def _areas(texts: common.Texts, names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return texts.required(FORMAT_LAST_AREA, areas=", ".join(names[:-1]), last=names[-1])


def country_label(texts: common.Texts, s: ShippingSettings, code: str) -> str:
    """The country as listed: its name, or e.g. "United States (except Alaska and North Dakota)"."""
    name = s.country_name(code)
    areas = s.excluded_areas.get(code)
    if not areas:
        return name
    names = [area_display_name(code, area) for area in areas]
    return texts.required(FORMAT_EXCLUDED, country=name, areas=_areas(texts, names))


def excluded_areas_text(texts: common.Texts, s: ShippingSettings) -> str:
    """Every area we do not ship to, written as #shipping writes them: "Alaska and North Dakota" ("A, B and C" for
    three). When areas of more than one country are excluded, each country's areas are followed by its name, in the
    order #shipping lists the countries: "Alaska and North Dakota (United States) and Yukon (Canada)". "" when nothing
    is excluded. Used by the announcement presets' ``{excluded_areas}``."""
    codes = [code for code in s.countries if s.excluded_areas.get(code)]
    phrases = [_areas(texts, [area_display_name(code, area) for area in s.excluded_areas[code]]) for code in codes]
    if len(phrases) > 1:
        phrases = [f"{phrase} ({s.country_name(code)})" for phrase, code in zip(phrases, codes, strict=True)]
    return _areas(texts, phrases) if phrases else ""


def group_section(texts: common.Texts, s: ShippingSettings, group: ShippingGroup) -> Section:
    full = [code for code in group.countries if code not in s.excluded_areas]
    partial = [code for code in group.countries if code in s.excluded_areas]
    lines = []
    for status, codes in (("full", full), ("partial", partial)):
        if codes:
            dot = texts.required(status_indicator_key(status))
            listed = COUNTRY_SEPARATOR.join(country_label(texts, s, code) for code in codes)
            lines.append(f"{dot} {listed}".strip())
    return Section(texts.required(group_name_key(group.id)), tuple(lines))


def not_available_section(texts: common.Texts, s: ShippingSettings) -> Section | None:
    others = texts.optional(NOT_AVAILABLE_OTHERS)
    names = [s.country_name(code) for code in s.not_available] + ([others] if others else [])
    if not names:
        return None
    dot = texts.required(status_indicator_key("none"))
    return Section(texts.required(NOT_AVAILABLE_HEADING), (f"{dot} {COUNTRY_SEPARATOR.join(names)}".strip(),))


def delivery_section(ui: Ui, texts: common.Texts, options: tuple[FulfillmentOption, ...]) -> Section | None:
    if not options:
        return None
    lines = []
    for option in options:
        price = ui.money(option.fee_minor) or ""
        lines.append(
            texts.required(
                FORMAT_DELIVERY,
                label=texts.required(option_key(option.id, "label")),
                price=price,
                description=texts.optional(option_key(option.id, "description")) or "",
            ).strip()
        )
    return Section(texts.required(DELIVERY_HEADING), tuple(lines))


def extras_section(ui: Ui, texts: common.Texts, extras: tuple[ExtraDef, ...]) -> Section | None:
    if not extras:
        return None
    lines = [
        texts.required(
            FORMAT_DELIVERY,
            label=texts.required(extra_key(extra.id, "label")),
            price=ui.money(extra.amount_minor) or "",
            description=texts.optional(extra_key(extra.id, "description")) or "",
        ).strip()
        for extra in extras
    ]
    return Section(texts.required(EXTRAS_HEADING), tuple(lines))


def shipping_card(ui: Ui, s: ShippingSettings, checkout: CheckoutSettings, texts: common.Texts) -> Card:
    """The card: countries by group, delivery, information, then the listed countries' areas."""
    legend = texts.optional(LEGEND)
    sections: list[Section | None] = [Section(None, (legend,)) if legend else None]
    sections += [group_section(texts, s, group) for group in s.groups]
    sections.append(not_available_section(texts, s))
    sections.append(delivery_section(ui, texts, checkout.fulfillment_options))
    sections.append(extras_section(ui, texts, checkout.extras))
    sections += [
        Section(texts.required(info_name_key(n)), (texts.required(info_value_key(n)),))
        for n in range(1, s.info_sections + 1)
    ]
    sections += [areas_section(texts, s, code) for code in s.list_areas]
    return Card(
        tone="info",
        title=texts.required(TITLE),
        intro=texts.required(INTRODUCTION),
        sections=tuple(section for section in sections if section is not None),
    )


def areas_section(texts: common.Texts, s: ShippingSettings, code: str) -> Section:
    """The country's areas (``shipping.list_areas``) as a section like a group's: its heading, the optional
    introduction, then the areas alphabetically on one line per status (available after the blue dot, the excluded
    ones after the grey dot)."""
    country = s.country_name(code)
    excluded = set(s.excluded_areas.get(code, ()))
    intro = texts.optional(AREAS_INTRODUCTION, country=country)
    lines = [intro] if intro else []
    for status, wanted in (("full", False), ("none", True)):
        # No-break spaces keep a name such as "New Jersey" on one line when the list wraps on a phone.
        names = [
            area_display_name(code, area).replace(" ", NO_BREAK_SPACE)
            for area in LISTED_AREAS[code]
            if (area in excluded) == wanted
        ]
        if names:
            dot = texts.required(status_indicator_key(status))
            lines.append(f"{dot} {COUNTRY_SEPARATOR.join(names)}".strip())
    return Section(texts.required(AREAS_HEADING, country=country), tuple(lines))


def shipping_messages(
    ui: Ui, s: ShippingSettings, checkout: CheckoutSettings, texts: common.Texts
) -> list[MessageSpec]:
    """The shipping card's messages: one, unless it is longer than one embed allows (see the module docstring); the
    banner closes the last one. Raises ``MissingContent``/``ChannelNotCreated`` at runtime when text or a channel is
    missing (nothing incomplete is posted)."""
    return common.guide_messages(ui, [shipping_card(ui, s, checkout, texts).block()], banner="shipping")


# -- runtime ---------------------------------------------------------------------------------------------------


def _build(app: App, guild: discord.Guild) -> list[MessageSpec]:
    return shipping_messages(app.ui, app.settings.shipping, app.settings.checkout, common.runtime_texts(app, guild))


async def publish(app: App) -> list[str]:
    return await common.publish_guide(
        app,
        feature=SHIPPING,
        prefix=PREFIX,
        channel_key=app.settings.shipping.channel,
        build=lambda guild: _build(app, guild),
    )


# -- preview ---------------------------------------------------------------------------------------------------

_MANY = ("US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "DE", "FR", "ES", "IT", "GB", "NL", "PL", "SE", "NO", "FI")
EXAMPLE_COUNTRIES = ShippingSettings(
    groups=tuple(ShippingGroup(f"example_{n}", _MANY) for n in range(1, 21)),
    excluded_areas={"US": ("AK", "ND", "HI")},
    not_available=("CU", "RU", "BY"),
    info_sections=10,
    list_areas=("US",),
)


def _sample(key: str) -> str | None:
    if key == FORMAT_EXCLUDED:
        return "{country} [sample except] {areas}"
    if key == FORMAT_LAST_AREA:
        return "{areas} [sample and] {last}"
    if key == FORMAT_DELIVERY:
        return "[sample option] {label} {price}"
    if key == AREAS_HEADING:
        return "[sample areas of] {country}"
    if key.startswith("shipping.info.") and key.endswith(".value"):
        return common.long_sample(f"sample information {key.split('.')[2]}", 600)
    return f"[{key}]"


def preview(app: App) -> list[PreviewState]:
    specs = shipping_messages(app.ui, app.settings.shipping, app.settings.checkout, common.preview_texts(app))
    states = common.preview_states(
        SHIPPING, "shipping", "The shipping card as posted in the shipping channel by /setup.", specs
    )
    states += common.preview_states(
        SHIPPING,
        "example_countries",
        "Synthetic example (not your configuration): 20 groups of 18 countries plus 10 long information sections "
        "continue in further embeds and messages instead of being cut.",
        shipping_messages(app.ui, EXAMPLE_COUNTRIES, app.settings.checkout, common.sample_texts(_sample)),
    )
    return states
