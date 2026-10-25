"""Pure builders for every verification message (no Discord calls).

The same functions build the live panel, the private replies, the direct message and the offline preview. At runtime
missing required text raises ``MissingContent`` (nothing incomplete is ever posted); empty optional text is omitted.
The preview passes ``preview=True`` to the panel resolver so a panel that is not fully configured still shows its
structure, with each missing value replaced by a visible ``[content.key]`` marker.

Customer data (the member's display name) is escaped with ``discord.utils.escape_markdown``; embeds never ping.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import VerificationSettings
from shopbot.features.verification import content_keys as k
from shopbot.ui.card import Card, Section, card_message, heading
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import ButtonSpec, ButtonStyle, MessageSpec, rows_of
from shopbot.ui.style import Ui

ROUTE = "verify"
AGREE = "agree"

Render = Callable[[str], str | None]
Values = Mapping[str, str | int]

_BUTTON_STYLES: dict[str, ButtonStyle] = {
    "primary": "primary",
    "secondary": "secondary",
    "success": "success",
    "danger": "danger",
}


def agree_id() -> str:
    """The panel button. It carries no record id: the clicking member is the subject."""
    return cid(ROUTE, AGREE)


# -- welcome panel --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    name: str
    value: str


@dataclass(frozen=True)
class PanelText:
    title: str
    description: str
    button_label: str
    rules: tuple[Rule, ...]
    rules_title: str | None = None
    rules_description: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    button_emoji: str | None = None
    after_heading: str | None = None
    after_text: str | None = None


def resolve_panel_text(render: Render, rule_count: int, *, preview: bool = False) -> PanelText:
    """Collect the panel's configured text (``render`` returns a key's text with links filled in, or None).

    Raises ``MissingContent`` for missing required text unless ``preview`` is set.
    """

    def required(key: str) -> str:
        value = render(key)
        if value:
            return value
        if preview:
            return f"[{key}]"
        raise MissingContent(key)

    rules = tuple(
        Rule(name=required(k.rule_name_key(n)), value=required(k.rule_value_key(n))) for n in range(1, rule_count + 1)
    )
    return PanelText(
        title=required(k.WELCOME_TITLE),
        description=required(k.WELCOME_DESCRIPTION),
        button_label=required(k.BUTTON_LABEL),
        rules=rules,
        rules_title=render(k.RULES_TITLE),
        rules_description=render(k.RULES_DESCRIPTION),
        image_url=render(k.WELCOME_IMAGE),
        thumbnail_url=render(k.WELCOME_THUMBNAIL),
        button_emoji=render(k.BUTTON_EMOJI),
        after_heading=render(k.AFTER_HEADING),
        after_text=render(k.AFTER_TEXT),
    )


def panel_message(ui: Ui, text: PanelText, *, button_style: str) -> MessageSpec:
    """One card under the welcome banner: the welcome text, the rules (each rule's name in bold before its text),
    what to do after verifying, and the Verify button."""
    rules = [text.rules_description or "", *(f"{heading(rule.name)} {rule.value}" for rule in text.rules)]
    card = Card(
        tone="primary",
        title=text.title,
        intro=text.description,
        sections=(
            Section(text.rules_title, tuple(line for line in rules if line)),
            Section(text.after_heading, (text.after_text or "",)) if text.after_text else Section(),
        ),
        image_url=text.image_url,
        thumbnail_url=text.thumbnail_url,
    )
    button = ButtonSpec(
        label=text.button_label,
        custom_id=agree_id(),
        style=_BUTTON_STYLES.get(button_style, "success"),
        emoji=text.button_emoji,
    )
    return card_message(ui, card, rows_of(button), banner="welcome")


def build_panel(ui: Ui, settings: VerificationSettings, links: Values) -> MessageSpec:
    """The live panel message. Raises ``MissingContent`` if required text is not configured."""

    def render(key: str) -> str | None:
        return ui.text(key, **links)

    return panel_message(ui, resolve_panel_text(render, settings.rules), button_style=settings.button_style)


# -- private replies and the direct message ---------------------------------------------------------------


def reply_values(links: Values, *, user_name: str, user_id: int) -> dict[str, str | int]:
    """Placeholder values for the replies: channel links plus the member (name escaped; it is customer data)."""
    return {**links, "user_name": escape_markdown(user_name), "user_mention": f"<@{user_id}>"}


def _reply(ui: Ui, title_key: str, description_key: str, values: Values, *, tone: str, banner: BannerId) -> MessageSpec:
    description = ui.text(description_key, **values)
    if description is None:
        raise MissingContent(description_key)
    title = ui.text(title_key, **values)
    return MessageSpec(
        embeds=(ui.embed(title=title, description=description, tone=tone, timestamp=True),), banner=ui.banner(banner)
    )


def confirmation_view(ui: Ui, values: Values) -> MessageSpec:
    """Private reply after the Member role was granted."""
    return _reply(
        ui, k.CONFIRMED_TITLE, k.CONFIRMED_DESCRIPTION, values, tone="success", banner="successful-confirmation"
    )


def already_verified_view(ui: Ui, values: Values) -> MessageSpec:
    """Private reply when the member already has the Member role (nothing changed)."""
    return _reply(ui, k.ALREADY_TITLE, k.ALREADY_DESCRIPTION, values, tone="info", banner="general-response")


def dm_view(ui: Ui, values: Values) -> MessageSpec:
    """Direct message confirming the verification (verification.dm_confirmation)."""
    return _reply(ui, k.DM_TITLE, k.DM_DESCRIPTION, values, tone="success", banner="successful-confirmation")
