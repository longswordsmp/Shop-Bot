"""Customer-facing content keys of ``/announce`` and its settings checks.

An announcement's title, message and field values are typed by staff when they publish it (runtime text, not
configuration). The configured wording is the heading of each configured extra field, ``announcements.field.<id>.name``
(required for every ``[[announcements.fields]]`` entry), the texts of each preset (``announcements.preset.<id>.*``,
required for every ``[[announcements.presets]]`` entry: title and description, plus the claim button's label and its
"offer ended" label when the preset has a coupon), and the shared branding footer/author line. A preset's title and
description may use ``{excluded_areas}``, the areas we do not ship to (``[shipping.excluded_areas]``, written as
#shipping writes them). Everything staff see while publishing (the form, the preset preview, confirmations, errors) is
operator text.
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import AnnouncementPreset, Settings
from shopbot.features.server_keys import channel_placeholders, reference_problems

ANNOUNCEMENTS = "announcements"
PRESET_TITLE = "title"
PRESET_DESCRIPTION = "description"
PRESET_CLAIM_BUTTON = "claim_button"
PRESET_ENDED_BUTTON = "ended_button"
COUPON_PLACEHOLDER = "coupon"  # a preset's description: the code of its coupon
# A preset's title and description: the areas we do not ship to ("Alaska and North Dakota"; ``presets.area_text``).
EXCLUDED_AREAS_PLACEHOLDER = "excluded_areas"
AREAS_DOC = (
    "{excluded_areas} is the list of areas we do not ship to right now, from [shipping.excluded_areas] and written as "
    "#shipping writes them (e.g. 'Alaska and North Dakota')."
)


def field_name_key(field_id: str) -> str:
    return f"announcements.field.{field_id}.name"


def preset_key(preset_id: str, part: str) -> str:
    """``announcements.preset.<id>.<part>`` (``PRESET_TITLE``, ``PRESET_DESCRIPTION``, ``PRESET_CLAIM_BUTTON``,
    ``PRESET_ENDED_BUTTON``)."""
    return f"announcements.preset.{preset_id}.{part}"


def preset_keys(settings: Settings, preset: AnnouncementPreset) -> list[ContentKey]:
    group = "Announcements: presets (posted with /announce preset:)"
    where = f"the '{preset.id}' preset announcement (/announce preset:{preset.id}, staff name '{preset.name}')"
    keys = [
        ck(
            preset_key(preset.id, PRESET_TITLE),
            Kind.TITLE,
            ANNOUNCEMENTS,
            group,
            f"Title of {where}, posted in the '{settings.announcements.channel}' channel. Plain text (no icons). "
            + AREAS_DOC,
            placeholders=(EXCLUDED_AREAS_PLACEHOLDER,),
        ),
        ck(
            preset_key(preset.id, PRESET_DESCRIPTION),
            Kind.DESCRIPTION,
            ANNOUNCEMENTS,
            group,
            f"Text of {where}. Discord formatting, icons ({{icon_gift}}), section headings and channel links "
            "({channel_catalog}) work here."
            + (
                f" {{coupon}} is the coupon's code ({preset.coupon}), e.g. 'press **Use coupon {{coupon}}** on the "
                "review step'."
                if preset.coupon
                else ""
            )
            + f" {AREAS_DOC}",
            placeholders=channel_placeholders(settings)
            + ((COUPON_PLACEHOLDER,) if preset.coupon else ())
            + (EXCLUDED_AREAS_PLACEHOLDER,),
        ),
    ]
    if preset.coupon:
        keys += [
            ck(
                preset_key(preset.id, PRESET_CLAIM_BUTTON),
                Kind.BUTTON_LABEL,
                ANNOUNCEMENTS,
                group,
                f"Button under {where} that claims the coupon {preset.coupon} (e.g. '{{icon_gift}} Claim coupon'). "
                "Pressing it answers privately with the coupon's terms (checkout.discount.claim.*).",
            ),
            ck(
                preset_key(preset.id, PRESET_ENDED_BUTTON),
                Kind.BUTTON_LABEL,
                ANNOUNCEMENTS,
                group,
                f"Label the button under {where} shows (disabled) once the coupon {preset.coupon} has ended or "
                "expired, e.g. 'Offer ended'.",
            ),
        ]
    return keys


def content_keys(settings: Settings) -> list[ContentKey]:
    an = settings.announcements
    group = "Announcements (posted with /announce)"
    keys: list[ContentKey] = []
    for item in an.fields:
        need = "staff must fill it in" if item.required else "shown only when staff fill it in"
        keys.append(
            ck(
                field_name_key(item.id),
                Kind.FIELD_NAME,
                ANNOUNCEMENTS,
                group,
                f"Heading of the '{item.id}' field of announcements posted with /announce in the '{an.channel}' "
                f"channel. The text under it is what staff type in the form's '{item.id}' input ({need}). Also used "
                "as that input's label in the staff form when it is at most 45 characters long.",
            )
        )
    for preset in an.presets:
        keys += preset_keys(settings, preset)
    return keys


def settings_problems(settings: Settings) -> list[str]:
    an = settings.announcements
    problems: list[str] = []
    if not an.channel:
        problems.append("announcements.channel: required (the channel key /announce posts to)")
    problems.extend(reference_problems(settings, channels=(("announcements.channel", an.channel),)))
    for path, keys in (("announcements.roles", an.roles), ("announcements.mention_roles", an.mention_roles)):
        problems.extend(f"{path}: {problem}" for problem in reference_problems(settings, roles=keys))
    return problems
