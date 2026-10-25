"""Preview: every verification UI state, built by the runtime view functions from synthetic sample data.

The welcome panel always shows its structure: missing text appears as ``[content.key]`` markers, and while
``verification.rules`` is not set yet an example with three rules is shown. Channel links use synthetic 19-digit
channel ids (the length of real ones) so the limits check is realistic. The replies are skipped while their text is
not configured (the content store records the missing keys for the checker).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.config.content_keys import Kind
from shopbot.features import core_keys as ck
from shopbot.features.server_keys import channel_mentions, channel_placeholder
from shopbot.features.verification import views
from shopbot.features.verification.content_keys import VERIFICATION
from shopbot.ui.spec import MessageSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

EXAMPLE_RULE_COUNT = 3
SAMPLE_MEMBER_ID = 1_000_000_000_000_000_001
SAMPLE_MEMBER_NAME = "[sample member]"
SAMPLE_ERROR_ID = "[sample error reference]"
_SAMPLE_CHANNEL_BASE = 1_000_000_000_000_000_100


def sample_links(app: App) -> dict[str, str]:
    """Channel links as /setup would provide them (real ones when known, otherwise synthetic channel ids)."""
    links = {
        channel_placeholder(channel.key): f"<#{_SAMPLE_CHANNEL_BASE + index}>"
        for index, channel in enumerate(app.settings.server.channels)
    }
    links.update(channel_mentions(app))
    return links


def preview(app: App) -> list[PreviewState]:
    ui = app.ui
    settings = app.settings.verification
    links = sample_links(app)
    registry = ui.content.registry
    states: list[PreviewState] = []

    def render(key: str) -> str | None:
        definition = registry.get(key)
        if definition is None:  # a rule beyond verification.rules (example structure)
            return None
        value = ui.text(key, **links)
        if definition.kind is Kind.URL and value and not value.startswith("https://"):
            return None  # an "unconfigured" marker is not an image address; the image is simply omitted
        return value

    rule_count = settings.rules or EXAMPLE_RULE_COUNT
    text = views.resolve_panel_text(render, rule_count, preview=True)
    panel_note = (
        "Welcome panel posted in the entry channel by /setup: welcome card, server rules and the 'I agree' button."
    )
    if not settings.rules:
        panel_note += f" verification.rules is not set yet: shown with {EXAMPLE_RULE_COUNT} example rules."
    states.append(
        PreviewState(
            feature=VERIFICATION,
            name="welcome_panel",
            note=panel_note,
            spec=views.panel_message(ui, text, button_style=settings.button_style),
        )
    )

    values = views.reply_values(links, user_name=SAMPLE_MEMBER_NAME, user_id=SAMPLE_MEMBER_ID)

    def add(name: str, note: str, build: Callable[[], MessageSpec | None]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        if spec is not None:
            states.append(PreviewState(feature=VERIFICATION, name=name, note=note, spec=spec))

    add(
        "confirmation",
        "Private reply after the member accepted the rules and received the Member role.",
        lambda: views.confirmation_view(ui, values),
    )
    add(
        "already_verified",
        "Private reply when the member clicks 'I agree' although they already have the Member role.",
        lambda: views.already_verified_view(ui, values),
    )
    add(
        "dm_confirmation",
        "Direct message after a successful verification (only when verification.dm_confirmation and "
        "features.dm_notifications are on).",
        lambda: views.dm_view(ui, values),
    )

    def error_state() -> MessageSpec | None:
        embed = ui.error_embed(ck.ERROR_GENERIC, error_id=SAMPLE_ERROR_ID)
        return MessageSpec(embeds=(embed,)) if embed is not None else None

    add(
        "error_cannot_grant",
        "Private reply when the bot cannot grant the Member role (role missing, not below the bot's role, or Discord "
        "refused); the reference matches the log and audit entry.",
        error_state,
    )
    return states
