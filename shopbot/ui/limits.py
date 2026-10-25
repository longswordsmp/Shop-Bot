"""Discord message, embed, component, and modal limits.

``validate_message`` / ``validate_modal`` report every violation. Nothing is truncated: text supplied by
the business is either sent exactly as written or not sent at all.

Limits reference: https://discord.com/developers/docs/resources/message#embed-object-embed-limits and
https://discord.com/developers/docs/components/reference
"""

from __future__ import annotations

from dataclasses import dataclass

from shopbot.ui.spec import ButtonSpec, EmbedSpec, MessageSpec, ModalSpec, SelectSpec

EMBEDS_PER_MESSAGE = 10
EMBED_TOTAL_CHARS = 6000
EMBED_TITLE = 256
EMBED_DESCRIPTION = 4096
EMBED_FIELDS = 25
EMBED_FIELD_NAME = 256
EMBED_FIELD_VALUE = 1024
EMBED_FOOTER = 2048
EMBED_AUTHOR = 256
ACTION_ROWS = 5
BUTTONS_PER_ROW = 5
CUSTOM_ID = 100
BUTTON_LABEL = 80
SELECT_OPTIONS = 25
SELECT_PLACEHOLDER = 150
OPTION_LABEL = 100
OPTION_VALUE = 100
OPTION_DESCRIPTION = 100
MODAL_TITLE = 45
MODAL_INPUTS = 5
INPUT_LABEL = 45
INPUT_DESCRIPTION = 100
INPUT_PLACEHOLDER = 100
INPUT_VALUE = 4000
URL_MAX = 2048


@dataclass(frozen=True)
class Violation:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _len(path: str, value: str | None, limit: int, out: list[Violation]) -> None:
    if value is not None and len(value) > limit:
        out.append(Violation(path, f"{len(value)} characters exceeds the limit of {limit}"))


def _blank(path: str, value: str | None, out: list[Violation]) -> None:
    if value is not None and not value.strip():
        out.append(Violation(path, "is blank"))


def validate_embed(embed: EmbedSpec, path: str = "embed") -> list[Violation]:
    out: list[Violation] = []
    if embed.is_empty():
        out.append(Violation(path, "embed has no title, description, fields, author, or image"))
    _len(f"{path}.title", embed.title, EMBED_TITLE, out)
    _len(f"{path}.description", embed.description, EMBED_DESCRIPTION, out)
    _len(f"{path}.author", embed.author_name, EMBED_AUTHOR, out)
    _len(f"{path}.footer", embed.footer_text, EMBED_FOOTER, out)
    if len(embed.fields) > EMBED_FIELDS:
        out.append(Violation(f"{path}.fields", f"{len(embed.fields)} fields exceeds the limit of {EMBED_FIELDS}"))
    for index, item in enumerate(embed.fields):
        fpath = f"{path}.fields[{index}]"
        if not item.name.strip():
            out.append(Violation(f"{fpath}.name", "field name is blank"))
        if not item.value.strip():
            out.append(Violation(f"{fpath}.value", "field value is blank"))
        _len(f"{fpath}.name", item.name, EMBED_FIELD_NAME, out)
        _len(f"{fpath}.value", item.value, EMBED_FIELD_VALUE, out)
    for attr in ("url", "author_icon_url", "footer_icon_url", "thumbnail_url", "image_url"):
        value = getattr(embed, attr)
        if value is not None:
            _len(f"{path}.{attr}", value, URL_MAX, out)
            if not value.startswith(("https://", "http://", "attachment://")):
                out.append(Violation(f"{path}.{attr}", "must be an http(s) or attachment URL"))
    return out


def _validate_custom_id(path: str, custom_id: str | None, seen: set[str], out: list[Violation]) -> None:
    if custom_id is None:
        out.append(Violation(path, "interactive component has no custom_id"))
        return
    if not custom_id:
        out.append(Violation(path, "custom_id is empty"))
    _len(path, custom_id, CUSTOM_ID, out)
    if custom_id in seen:
        out.append(Violation(path, f"duplicate custom_id {custom_id!r}"))
    seen.add(custom_id)


def validate_message(spec: MessageSpec) -> list[Violation]:
    out: list[Violation] = []
    if not spec.embeds:
        out.append(Violation("message", "every bot message must contain at least one embed"))
    if len(spec.embeds) > EMBEDS_PER_MESSAGE:
        out.append(Violation("message.embeds", f"{len(spec.embeds)} embeds exceeds {EMBEDS_PER_MESSAGE}"))
    total = 0
    for index, embed in enumerate(spec.embeds):
        out.extend(validate_embed(embed, f"embeds[{index}]"))
        total += embed.text_length()
    if total > EMBED_TOTAL_CHARS:
        out.append(Violation("message.embeds", f"combined embed text {total} exceeds {EMBED_TOTAL_CHARS}"))

    if len(spec.rows) > ACTION_ROWS:
        out.append(Violation("message.rows", f"{len(spec.rows)} action rows exceeds {ACTION_ROWS}"))
    seen: set[str] = set()
    for r_index, row in enumerate(spec.rows):
        rpath = f"rows[{r_index}]"
        if not row:
            out.append(Violation(rpath, "empty action row"))
            continue
        selects = [c for c in row if isinstance(c, SelectSpec)]
        if selects and len(row) > 1:
            out.append(Violation(rpath, "a select menu must be alone in its action row"))
        if len(row) > BUTTONS_PER_ROW:
            out.append(Violation(rpath, f"{len(row)} buttons exceeds {BUTTONS_PER_ROW} per row"))
        for c_index, component in enumerate(row):
            cpath = f"{rpath}[{c_index}]"
            if isinstance(component, ButtonSpec):
                if not component.label and not component.emoji:
                    out.append(Violation(cpath, "button has neither label nor emoji"))
                _blank(f"{cpath}.label", component.label, out)
                _len(f"{cpath}.label", component.label, BUTTON_LABEL, out)
                if component.style == "link":
                    if not component.url:
                        out.append(Violation(cpath, "link button has no URL"))
                    elif not component.url.startswith(("https://", "http://", "discord://")):
                        out.append(Violation(f"{cpath}.url", "link button URL must be http(s)"))
                    if component.custom_id is not None:
                        out.append(Violation(cpath, "link buttons cannot have a custom_id"))
                else:
                    _validate_custom_id(f"{cpath}.custom_id", component.custom_id, seen, out)
            else:
                _validate_custom_id(f"{cpath}.custom_id", component.custom_id, seen, out)
                _len(f"{cpath}.placeholder", component.placeholder, SELECT_PLACEHOLDER, out)
                if not component.options:
                    out.append(Violation(cpath, "select menu has no options"))
                if len(component.options) > SELECT_OPTIONS:
                    out.append(Violation(cpath, f"{len(component.options)} options exceeds {SELECT_OPTIONS}"))
                if not (0 <= component.min_values <= component.max_values <= max(1, len(component.options))):
                    out.append(Violation(cpath, "min_values/max_values are inconsistent with the options"))
                values: set[str] = set()
                for o_index, option in enumerate(component.options):
                    opath = f"{cpath}.options[{o_index}]"
                    _blank(f"{opath}.label", option.label, out)
                    _len(f"{opath}.label", option.label, OPTION_LABEL, out)
                    _len(f"{opath}.value", option.value, OPTION_VALUE, out)
                    _len(f"{opath}.description", option.description, OPTION_DESCRIPTION, out)
                    if option.value in values:
                        out.append(Violation(f"{opath}.value", f"duplicate option value {option.value!r}"))
                    values.add(option.value)
    return out


def validate_modal(spec: ModalSpec) -> list[Violation]:
    out: list[Violation] = []
    _validate_custom_id("modal.custom_id", spec.custom_id, set(), out)
    if not spec.title.strip():
        out.append(Violation("modal.title", "is blank"))
    _len("modal.title", spec.title, MODAL_TITLE, out)
    if not spec.inputs:
        out.append(Violation("modal.inputs", "a form needs at least one input"))
    if len(spec.inputs) > MODAL_INPUTS:
        out.append(Violation("modal.inputs", f"{len(spec.inputs)} inputs exceeds {MODAL_INPUTS}"))
    seen: set[str] = set()
    for index, item in enumerate(spec.inputs):
        ipath = f"modal.inputs[{index}]"
        _validate_custom_id(f"{ipath}.custom_id", item.custom_id, seen, out)
        if not item.label.strip():
            out.append(Violation(f"{ipath}.label", "is blank"))
        _len(f"{ipath}.label", item.label, INPUT_LABEL, out)
        _len(f"{ipath}.description", item.description, INPUT_DESCRIPTION, out)
        _len(f"{ipath}.placeholder", item.placeholder, INPUT_PLACEHOLDER, out)
        _len(f"{ipath}.default", item.default, INPUT_VALUE, out)
        if item.max_length is not None and not (1 <= item.max_length <= INPUT_VALUE):
            out.append(Violation(f"{ipath}.max_length", f"must be between 1 and {INPUT_VALUE}"))
        if item.min_length is not None and not (0 <= item.min_length <= INPUT_VALUE):
            out.append(Violation(f"{ipath}.min_length", f"must be between 0 and {INPUT_VALUE}"))
        if item.min_length is not None and item.max_length is not None and item.min_length > item.max_length:
            out.append(Violation(f"{ipath}", "min_length exceeds max_length"))
    return out
