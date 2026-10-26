"""Custom emoji (the bot's icons) never reach the parts of an embed where Discord prints them as raw text."""

from __future__ import annotations

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import Kind, Registry, ck
from shopbot.core.clock import FakeClock
from shopbot.features.operations.views import link_button
from shopbot.ui.component_emoji import strip_custom_emoji
from shopbot.ui.discord_render import to_embed
from shopbot.ui.spec import EmbedField, EmbedSpec
from shopbot.ui.style import Ui
from tests.helpers import make_settings
from tools.build_preview import render_embed

TRUCK = "<:truck:111111111111111111>"
SPIN = "<a:spin:222222222222222222>"
PICTURE = '<img class="cemoji" src="emojis/truck.png" alt=":truck:" title=":truck:">'


def test_strip_custom_emoji_keeps_the_text_and_unicode_emoji() -> None:
    assert strip_custom_emoji(f"{TRUCK} Shipping") == "Shipping"
    assert strip_custom_emoji(f"Order {SPIN} shipped") == "Order shipped"
    assert strip_custom_emoji(f"{TRUCK}{SPIN}  Fast") == "Fast"
    assert strip_custom_emoji("🔒 Ticket #0001 closed") == "🔒 Ticket #0001 closed"
    assert strip_custom_emoji(TRUCK) is None
    assert strip_custom_emoji(None) is None
    assert strip_custom_emoji("") == ""
    assert strip_custom_emoji(" Step 1 of 3 ") == " Step 1 of 3 "  # nothing to strip: unchanged


def _embed() -> EmbedSpec:
    return EmbedSpec(
        title=f"{TRUCK} Shipping",
        description=f"{TRUCK} We ship fast.",
        author_name=f"{SPIN} AmazonYaarts",
        footer_text=f"{TRUCK} Step 2 of 5",
        fields=(EmbedField(name=f"{TRUCK} DELIVERY", value=f"{TRUCK} Standard"),),
    )


def test_discord_gets_no_custom_emoji_in_title_author_and_footer() -> None:
    embed = to_embed(_embed())
    assert embed.title == "Shipping"
    assert embed.author.name == "AmazonYaarts"
    assert embed.footer.text == "Step 2 of 5"
    # Descriptions, field names and field values show them.
    assert embed.description == f"{TRUCK} We ship fast."
    assert embed.fields[0].name == f"{TRUCK} DELIVERY"
    assert embed.fields[0].value == f"{TRUCK} Standard"


def test_an_icon_only_title_is_left_out() -> None:
    assert to_embed(EmbedSpec(title=TRUCK, description="x")).title is None


def test_the_preview_shows_what_discord_shows() -> None:
    page = render_embed(_embed())
    assert '<div class="embed-title">Shipping</div>' in page
    assert f'<div class="field-name">{PICTURE} DELIVERY</div>' in page
    assert "<span>AmazonYaarts</span>" in page
    assert "&lt;:truck:" not in page and "&lt;a:spin:" not in page


def test_staff_alert_link_buttons_render_a_leading_icon() -> None:
    key = ck("x.open", Kind.BUTTON_LABEL, "core", "g", "doc")
    content = ContentStore(
        Registry([key]),
        {"x.open": "{icon_link} Open ticket"},
        icons=lambda name: "<:link:333333333333333333>" if name == "link" else "",
        icon_names=lambda: ("link",),
    )
    settings = make_settings()
    ui = Ui(lambda: settings, content, FakeClock())
    button = link_button(ui, "x.open", "https://discord.com/channels/1/2/3")
    assert button is not None
    assert button.label == "<:link:333333333333333333> Open ticket"  # to_view turns the icon into the emoji
