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

from shopbot.config.settings import CheckoutSettings, FormField
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
    parts = answer_key.split(".")
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
    steps = [m.BROWSE, m.CART]
    if settings.fulfillment_options:
        steps.append(m.FULFILLMENT)
    if form_inputs(settings, option_id):
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
