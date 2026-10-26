"""Pure workflow rules: which steps a session goes through and which form fields it asks for.

Steps: browse -> cart -> fulfillment (only when ``checkout.fulfillment_options`` exist) -> details (only when
``checkout.details_form`` or the chosen option's form has fields) -> review.

Form answers are stored under stable keys: ``d.<field>`` for ``checkout.details_form`` fields,
``o.<option>.<field>`` for a fulfillment option's form fields and ``s.<field>`` for ``checkout.submission_form``
fields (asked in a form when the customer presses Submit; stored with the order like the others). A Discord form
holds at most 5 inputs, so the details fields are split into parts of 5 (at most two parts, since each configured
form has at most 5 fields); the submission form has at most 5 fields and is always one form.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from shopbot.config.settings import CheckoutSettings, ExtraDef, FeeDef, FormField, ShippingSettings
from shopbot.core.countries import matches_area
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m

INPUTS_PER_FORM = 5


@dataclass(frozen=True)
class FormInput:
    answer_key: str
    field: FormField
    label_key: str
    description_key: str
    placeholder_key: str


def form_inputs(settings: CheckoutSettings, option_id: str | None) -> list[FormInput]:
    """Every form field the session asks for: the details form, then the chosen option's form."""
    inputs = [
        FormInput(
            answer_key=f"d.{f.id}",
            field=f,
            label_key=k.form_field_key(f.id, "label"),
            description_key=k.form_field_key(f.id, "description"),
            placeholder_key=k.form_field_key(f.id, "placeholder"),
        )
        for f in settings.details_form
    ]
    option = settings.fulfillment(option_id)
    if option is not None:
        inputs += [
            FormInput(
                answer_key=f"o.{option.id}.{f.id}",
                field=f,
                label_key=k.option_form_key(option.id, f.id, "label"),
                description_key=k.option_form_key(option.id, f.id, "description"),
                placeholder_key=k.option_form_key(option.id, f.id, "placeholder"),
            )
            for f in option.form
        ]
    return inputs


SUBMISSION_PREFIX = "s."


def submission_inputs(settings: CheckoutSettings) -> list[FormInput]:
    """The fields of ``checkout.submission_form`` (asked when the customer presses Submit)."""
    return [
        FormInput(
            answer_key=f"{SUBMISSION_PREFIX}{f.id}",
            field=f,
            label_key=k.submission_field_key(f.id, "label"),
            description_key=k.submission_field_key(f.id, "description"),
            placeholder_key=k.submission_field_key(f.id, "placeholder"),
        )
        for f in settings.submission_form
    ]


def form_parts(inputs: Sequence[FormInput]) -> list[list[FormInput]]:
    return [list(inputs[i : i + INPUTS_PER_FORM]) for i in range(0, len(inputs), INPUTS_PER_FORM)]


def label_key_for_answer(answer_key: str) -> str | None:
    """The content key that labels a stored answer (None for an unrecognized key)."""
    if answer_key == "a.country":
        return k.ADDRESS_COUNTRY_LABEL
    parts = answer_key.split(".")
    if len(parts) == 2 and parts[0] == "a":
        return k.address_field_key(parts[1], "label")
    if len(parts) == 2 and parts[0] == "d":
        return k.form_field_key(parts[1], "label")
    if len(parts) == 2 and parts[0] == "s":
        return k.submission_field_key(parts[1], "label")
    if len(parts) == 3 and parts[0] == "o":
        return k.option_form_key(parts[1], parts[2], "label")
    return None


def details_complete(inputs: Sequence[FormInput], answers: Mapping[str, str]) -> bool:
    return all(answers.get(i.answer_key, "").strip() for i in inputs if i.field.required)


def relevant_answers(inputs: Sequence[FormInput], answers: Mapping[str, str]) -> dict[str, str]:
    """The non-empty answers of the fields the session currently asks for (what an order stores)."""
    return {i.answer_key: answers[i.answer_key] for i in inputs if answers.get(i.answer_key, "").strip()}


def steps_for(settings: CheckoutSettings, option_id: str | None) -> tuple[str, ...]:
    """browse, cart, [fulfillment], [details], review. With ``ask_address`` the details step is the shipping address
    and comes before the delivery options (the destination is known when choosing how fast it ships); options
    with their own forms are then not supported (settings problem)."""
    steps = [m.BROWSE, m.CART]
    if settings.ask_address:
        steps.append(m.DETAILS)
    if settings.fulfillment_options:
        steps.append(m.FULFILLMENT)
    if not settings.ask_address and form_inputs(settings, option_id):
        steps.append(m.DETAILS)
    steps.append(m.REVIEW)
    return tuple(steps)


def chosen_option_valid(settings: CheckoutSettings, option_id: str | None) -> bool:
    return not settings.fulfillment_options or settings.fulfillment(option_id) is not None


def previous_step(settings: CheckoutSettings, option_id: str | None, step: str) -> str | None:
    steps = steps_for(settings, option_id)
    if step not in steps:
        return None
    index = steps.index(step)
    return steps[index - 1] if index > 0 else None


def next_step(settings: CheckoutSettings, option_id: str | None, step: str) -> str | None:
    steps = steps_for(settings, option_id)
    if step not in steps:
        return None
    index = steps.index(step)
    return steps[index + 1] if index + 1 < len(steps) else None


# -- extras ------------------------------------------------------------------------------------------------------
# A chosen extra is stored in the session's details as ``x.<extra>`` = "1" and becomes an order fee with the fee id
# ``x.<extra>`` (its label is the extra's label), so the totals, the order and the staff record treat it like a fee.

EXTRA_PREFIX = "x."


def extra_key(extra_id: str) -> str:
    return f"{EXTRA_PREFIX}{extra_id}"


def chosen_extras(settings: CheckoutSettings, details: Mapping[str, str]) -> tuple[ExtraDef, ...]:
    """The extras the customer added (in configured order; an extra no longer configured is ignored)."""
    return tuple(extra for extra in settings.extras if details.get(extra_key(extra.id)) == "1")


def session_fees(settings: CheckoutSettings, details: Mapping[str, str]) -> tuple[FeeDef, ...]:
    """The configured fees plus the chosen extras (flat fees with the id ``x.<extra>``)."""
    extras = tuple(FeeDef(id=extra_key(e.id), amount_minor=e.amount_minor) for e in chosen_extras(settings, details))
    return (*settings.fees, *extras)


def extra_id_of_fee(fee_id: str) -> str | None:
    """The extra a fee stands for (None for a configured fee)."""
    return fee_id.removeprefix(EXTRA_PREFIX) if fee_id.startswith(EXTRA_PREFIX) else None


# -- shipping address ----------------------------------------------------------------------------------------
# With ``checkout.ask_address`` the details step asks for the shipping address. The country is chosen from menus
# (``a.group`` remembers the region menu's choice; ``a.country`` is the country code) and the rest is typed in a
# private form; every answer is stored under ``a.<field>``. Apartment details (unit, building or floor, entry code,
# delivery instructions) are optional and typed in a second private form; they are part of the address (stored,
# kept and shown like it, never in a channel).

ADDRESS_PREFIX = "a."
ADDRESS_GROUP = "a.group"
ADDRESS_COUNTRY = "a.country"


@dataclass(frozen=True)
class AddressField:
    id: str
    style: str
    min_length: int
    max_length: int
    required: bool = True

    @property
    def answer_key(self) -> str:
        return f"{ADDRESS_PREFIX}{self.id}"


# The form holds at most 5 inputs: exactly these. ``region`` (state / province) is required only for a country with
# excluded areas (the bot must check it).
ADDRESS_FIELDS: tuple[AddressField, ...] = (
    AddressField("name", "short", 2, 100),
    AddressField("street", "paragraph", 3, 200),
    AddressField("city", "short", 1, 100),
    AddressField("region", "short", 0, 100, required=False),
    AddressField("postal", "short", 2, 20),
)
# The apartment details form: every field optional (an emptied field is removed). ``access`` is an entry or buzzer
# code: as private as the address.
APARTMENT_FIELDS: tuple[AddressField, ...] = (
    AddressField("unit", "short", 0, 50, required=False),
    AddressField("building", "short", 0, 50, required=False),
    AddressField("access", "short", 0, 50, required=False),
    AddressField("notes", "paragraph", 0, 200, required=False),
)


def _address_key(field_id: str) -> str:
    return f"{ADDRESS_PREFIX}{field_id}"


# Every stored part of the address in shipping-label order (how staff read it): the label's lines, the country, then
# what the courier needs at the door on their own lines.
ADDRESS_LABEL_ORDER: tuple[str, ...] = (
    *(_address_key(i) for i in ("name", "street", "unit", "building", "city", "region", "postal")),
    ADDRESS_COUNTRY,
    *(_address_key(i) for i in ("access", "notes")),
)
COUNTRY_MENU_SIZE = 25  # options per Discord menu: a larger region is split into several country menus


def address_field_required(field: AddressField, shipping: ShippingSettings, country: str | None) -> bool:
    return field.required or (field.id == "region" and country is not None and country in shipping.excluded_areas)


def excluded_area(shipping: ShippingSettings, country: str | None, region: str) -> str | None:
    """The excluded area (e.g. "AK") the typed state/region names, or None when the address can be shipped to."""
    if country is None:
        return None
    return next(
        (area for area in shipping.excluded_areas.get(country, ()) if matches_area(country, area, region)), None
    )


def address_problem(shipping: ShippingSettings, details: Mapping[str, str]) -> str | None:
    """Why the stored address cannot be used: "country" (none chosen, or no longer one we ship to), "missing" (a
    required answer is empty) or "excluded" (an area we do not ship to); None when it is complete."""
    country = details.get(ADDRESS_COUNTRY)
    if not country or not shipping.ships_to(country):
        return "country"
    for field in ADDRESS_FIELDS:
        if address_field_required(field, shipping, country) and not details.get(field.answer_key, "").strip():
            return "missing"
    if excluded_area(shipping, country, details.get(f"{ADDRESS_PREFIX}region", "")) is not None:
        return "excluded"
    return None


def address_answers(details: Mapping[str, str]) -> dict[str, str]:
    """The address as an order stores it, in shipping-label order: the typed fields, the apartment details and the
    country code (the region menu's choice is not stored)."""
    return {key: details[key] for key in ADDRESS_LABEL_ORDER if details.get(key, "").strip()}


def has_apartment(details: Mapping[str, str]) -> bool:
    """Any apartment detail was entered."""
    return any(details.get(f.answer_key, "").strip() for f in APARTMENT_FIELDS)


def country_menus(shipping: ShippingSettings, group_id: str | None) -> list[tuple[str, ...]]:
    """The country codes of the chosen region, in menus of at most 25 (none before a region is chosen)."""
    group = next((g for g in shipping.groups if g.id == group_id), None)
    if group is None:
        return []
    codes = group.countries
    return [codes[i : i + COUNTRY_MENU_SIZE] for i in range(0, len(codes), COUNTRY_MENU_SIZE)]


def details_step_complete(
    settings: CheckoutSettings, shipping: ShippingSettings, option_id: str | None, details: Mapping[str, str]
) -> bool:
    """The details step is done: the details forms answered and (with ``ask_address``) a usable address."""
    if not details_complete(form_inputs(settings, option_id), details):
        return False
    return not settings.ask_address or address_problem(shipping, details) is None
