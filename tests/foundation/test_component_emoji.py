"""An icon at the start of a button or select option label: validated, rendered, shown as the component's emoji."""

from __future__ import annotations

import types
from typing import Any

import discord

from shopbot.config.content import ContentStore, validate_value
from shopbot.config.content_keys import Kind, Registry, ck
from shopbot.core.clock import FakeClock
from shopbot.ui.component_emoji import split_button, split_label, split_option
from shopbot.ui.discord_render import to_view
from shopbot.ui.limits import validate_message
from shopbot.ui.routing import RoutedItem
from shopbot.ui.spec import ButtonSpec, EmbedSpec, MessageSpec, SelectOptionSpec, SelectSpec
from shopbot.ui.style import Ui
from tests.helpers import make_settings
from tools.build_preview import render_components

CART = "<:cart:111111111111111111>"
ICONS = ("cart", "tag", "dot_blue")
BUTTON = ck("x.button", Kind.BUTTON_LABEL, "core", "g", "doc")
OPTION = ck("x.option", Kind.OPTION_LABEL, "core", "g", "doc")
LINK_LABEL = ck("x.link.label", Kind.BUTTON_LABEL, "core", "g", "doc")
LINK_URL = ck("x.link.url", Kind.URL, "core", "g", "doc")
BUTTON_EMOJI = ck("x.button_emoji", Kind.EMOJI, "core", "g", "doc", required=False)


def store(values: dict[str, str], uploaded: dict[str, int] | None = None) -> ContentStore:
    """A content store whose icons are ``ICONS``; ``uploaded`` (name -> emoji id) are the uploaded ones."""
    ids = {"cart": 111111111111111111} if uploaded is None else uploaded

    def icon_text(name: str) -> str:
        if name in ids:
            return f"<:{name}:{ids[name]}>"
        return "•" if name.startswith("dot_") else ""

    registry = Registry([BUTTON, OPTION, LINK_LABEL, LINK_URL, BUTTON_EMOJI])
    return ContentStore(registry, values, icons=icon_text, icon_names=lambda: ICONS)


def make_ui(content: ContentStore) -> Ui:
    settings = make_settings()
    return Ui(lambda: settings, content, FakeClock())


def message(*rows: tuple[Any, ...]) -> MessageSpec:
    return MessageSpec(embeds=(EmbedSpec(title="t"),), rows=rows)


def test_a_label_may_start_with_one_icon_only() -> None:
    assert validate_value(BUTTON, "{icon_cart} Start order", ICONS) == []
    assert validate_value(BUTTON, "{icon_cart}", ICONS) == []  # an icon-only button
    assert validate_value(OPTION, "{icon_cart} Cart", ICONS) == []
    for wrong in ("Start {icon_cart} order", "Start order {icon_cart}", "{icon_cart}Start", "{icon_cart} {icon_tag} x"):
        problems = validate_value(BUTTON, wrong, ICONS)
        assert len(problems) == 1 and "one icon only, at the very start" in problems[0], wrong
        assert "button's emoji" in problems[0]
    assert "option's emoji" in validate_value(OPTION, "Cart {icon_cart}", ICONS)[0]
    assert validate_value(OPTION, "{icon_cart}", ICONS) == [
        "needs a label after its icon (a select option cannot show only an icon)"
    ]
    # Unknown icons are still reported when the names are known (and only then).
    assert validate_value(BUTTON, "{icon_nope} Buy", ICONS) == ["uses icons that do not exist: nope"]
    assert validate_value(BUTTON, "{icon_nope} Buy") == []
    assert "icons that do not exist: nope" in validate_value(BUTTON, "Buy {icon_nope}", ICONS)[1]
    # Other component texts still refuse icons.
    placeholder = ck("x.placeholder", Kind.SELECT_PLACEHOLDER, "core", "g", "doc")
    assert "cannot show icons here" in validate_value(placeholder, "{icon_cart} Pick", ICONS)[0]


def test_the_length_limit_counts_the_label_without_its_icon() -> None:
    assert validate_value(BUTTON, "{icon_cart} " + "x" * 80, ICONS) == []
    assert validate_value(BUTTON, "{icon_cart} " + "x" * 81, ICONS) == [
        "is 81 characters (not counting its icon); Discord allows at most 80 here"
    ]
    assert validate_value(OPTION, "{icon_cart} " + "x" * 100, ICONS) == []
    assert validate_value(BUTTON, "x" * 81) == ["is 81 characters; Discord allows at most 80 here"]


def test_labels_render_their_icon_and_its_fallback() -> None:
    content = store(
        {"x.button": "{icon_cart} Start order", "x.option": "{icon_tag} Deals", "x.link.label": "{icon_dot_blue} Open"}
    )
    assert content.render("x.button") == f"{CART} Start order"
    assert content.render("x.option") == "Deals"  # not uploaded: a section icon shows nothing (nor its space)
    assert content.render("x.link.label") == "• Open"  # a status dot keeps its plain dot, as in every text
    content.replace(file_values={"x.button": "{icon_cart}"})
    assert content.render("x.button") == CART
    content.replace(file_values={"x.button": "{icon_tag}"})
    assert content.render("x.button") is None  # only an icon that is not uploaded: nothing to show


def test_ui_buttons_keep_the_rendered_label_and_drop_an_icon_only_button_without_its_icon() -> None:
    content = store(
        {"x.button": "{icon_cart} Start order", "x.link.label": "{icon_cart} Shop", "x.link.url": "https://e.x"}
    )
    ui = make_ui(content)
    assert ui.button("x.button", "sb:x:go") == ButtonSpec(label=f"{CART} Start order", custom_id="sb:x:go")
    link = ui.link_button("x.link.label", "x.link.url")
    assert link is not None and link.label == f"{CART} Shop"
    content.replace(file_values={"x.button": "{icon_tag}"})
    assert ui.button("x.button", "sb:x:go") is None
    content.replace(file_values={"x.button": "{icon_tag}", "x.button_emoji": "🛒"})
    assert ui.button("x.button", "sb:x:go", emoji_key="x.button_emoji") == ButtonSpec(
        label=None, custom_id="sb:x:go", emoji="🛒"
    )


def test_a_leading_custom_emoji_becomes_the_components_emoji() -> None:
    assert split_label(f"{CART} Start order", None) == ("Start order", CART)
    assert split_label(f"  {CART}   Start order  ", None) == ("Start order", CART)
    assert split_label("<a:spin:222222222222222222> Wait", None) == ("Wait", "<a:spin:222222222222222222>")
    assert split_label(CART, None) == (None, CART)  # icon only
    assert split_label(f"{CART} Start order", "🛒") == ("Start order", "🛒")  # the emoji key wins
    # Fallbacks and other labels are left as they are.
    assert split_label("Start order", None) == ("Start order", None)
    assert split_label("• Open", None) == ("• Open", None)
    # A custom emoji after the start (e.g. from a placeholder value) would show as raw text: it is left out.
    assert split_label(f"Start {CART}", None) == ("Start", None)
    assert split_label(f"{CART} Add {CART} protection", None) == ("Add protection", CART)
    assert split_label(None, "🛒") == (None, "🛒")

    button = ButtonSpec(label=f"{CART} Start order", custom_id="sb:x:go", style="primary", disabled=True)
    assert split_button(button) == ButtonSpec(
        label="Start order", custom_id="sb:x:go", style="primary", emoji=CART, disabled=True
    )
    link = ButtonSpec(label=f"{CART} Shop", style="link", url="https://e.x")
    assert split_button(link) == ButtonSpec(label="Shop", style="link", url="https://e.x", emoji=CART)
    plain = ButtonSpec(label="Shop", custom_id="sb:x:go")
    assert split_button(plain) is plain

    option = SelectOptionSpec(label=f"{CART} Cart", value="cart", description="d", default=True)
    assert split_option(option) == SelectOptionSpec(
        label="Cart", value="cart", description="d", emoji=CART, default=True
    )
    icon_only = SelectOptionSpec(label=CART, value="cart")
    assert split_option(icon_only) is icon_only  # an option needs a label


def test_to_view_sends_the_icon_as_the_emoji() -> None:
    spec = message(
        (
            ButtonSpec(label=f"{CART} Start order", custom_id="sb:x:go", style="primary"),
            ButtonSpec(label=f"{CART} Shop", style="link", url="https://e.x"),
            ButtonSpec(label=CART, custom_id="sb:x:icon"),
        ),
        (SelectSpec(custom_id="sb:x:pick", options=(SelectOptionSpec(label=f"{CART} Cart", value="cart"),)),),
    )
    view = to_view(spec)
    assert view is not None
    items = [c.item if isinstance(c, RoutedItem) else c for c in view.children]
    buttons = [i for i in items if isinstance(i, discord.ui.Button)]
    assert [(b.label, str(b.emoji)) for b in buttons] == [("Start order", CART), ("Shop", CART), (None, CART)]
    assert all(b.emoji is not None and b.emoji.id == 111111111111111111 and b.emoji.name == "cart" for b in buttons)
    select = next(i for i in items if isinstance(i, discord.ui.Select))
    assert [(o.label, str(o.emoji)) for o in select.options] == [("Cart", CART)]


def test_limits_check_the_label_without_its_icon() -> None:
    long_button = ButtonSpec(label=f"{CART} " + "x" * 80, custom_id="sb:x:a")
    icon_only = ButtonSpec(label=CART, custom_id="sb:x:b")
    options = (SelectOptionSpec(label=f"{CART} " + "y" * 100, value="v"),)
    assert validate_message(message((long_button, icon_only), (SelectSpec(custom_id="sb:x:s", options=options),))) == []
    too_long = ButtonSpec(label=f"{CART} " + "x" * 81, custom_id="sb:x:a")
    long_option = SelectOptionSpec(label=f"{CART} " + "y" * 101, value="v")
    violations = [str(v) for v in validate_message(message((too_long,), (SelectSpec("sb:x:s", (long_option,)),)))]
    assert violations == [
        "rows[0][0].label: 81 characters exceeds the limit of 80",
        "rows[1][0].options[0].label: 101 characters exceeds the limit of 100",
    ]


def test_the_preview_draws_the_icon_on_buttons_and_options() -> None:
    spec = message(
        (ButtonSpec(label=f"{CART} Start order", custom_id="sb:x:go"), ButtonSpec(label=CART, custom_id="sb:x:icon")),
        (SelectSpec(custom_id="sb:x:pick", options=(SelectOptionSpec(label=f"{CART} Cart", value="cart"),)),),
    )
    router: Any = types.SimpleNamespace(routes=lambda: [])
    page = render_components(spec, router)
    picture = '<img class="cemoji" src="emojis/cart.png" alt=":cart:" title=":cart:">'
    assert f'<span class="emoji">{picture}</span>Start order</button>' in page
    assert f'<span class="emoji">{picture}</span></button>' in page  # icon only
    assert f'<span class=emoji>{picture}</span><div><div class="opt-label">Cart</div>' in page
    assert "&lt;:cart:" not in page
