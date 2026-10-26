"""The house-style checker (tools.check_quality): each [checked] rule of docs/QUALITY_STANDARDS.md catches its
violation and lets a good message through."""

from __future__ import annotations

from shopbot.ui.spec import (
    ButtonSpec,
    EmbedField,
    EmbedSpec,
    MessageSpec,
    ModalSpec,
    PreviewState,
    SelectOptionSpec,
    SelectSpec,
    TextInputSpec,
)
from tools.check_quality import check_states, collect, is_customer_message
from tools.offline import ROOT

FOOTER = "AmazonYaarts"
CART = "<:cart:111111111111111111>"


def card(**kwargs: object) -> EmbedSpec:
    values: dict[str, object] = {"title": "Your cart", "description": "Check your items.", "footer_text": FOOTER}
    values.update(kwargs)
    return EmbedSpec(**values)  # type: ignore[arg-type]


def rules(*specs: MessageSpec | ModalSpec, name: str = "state") -> list[str]:
    states = [PreviewState("checkout", f"{name}_{i}", "note", spec) for i, spec in enumerate(specs)]
    return sorted(f.rule for f in check_states(states, FOOTER))


def test_a_good_customer_card_passes() -> None:
    spec = MessageSpec(
        embeds=(card(description=f"**{CART} ITEMS**\n`2×` **Mug** · $6.98\n\n**Total · $6.98**"),),
        rows=((ButtonSpec(label=f"{CART} Start order", custom_id="sb:x:a", style="success"),),),
    )
    assert rules(spec) == []


def test_customer_and_operator_messages_are_told_apart() -> None:
    assert is_customer_message(MessageSpec(embeds=(card(),)), FOOTER)
    operator = MessageSpec(embeds=(EmbedSpec(title="Product #3 created", timestamp=1),))
    assert not is_customer_message(operator, FOOTER)
    continuation = MessageSpec(embeds=(EmbedSpec(description="more lines"),))  # a later message of a long card
    assert is_customer_message(continuation, FOOTER)


def test_i1_icons_where_discord_cannot_show_them() -> None:
    assert rules(MessageSpec(embeds=(card(title=f"{CART} Your cart"),))) == ["I1"]
    assert rules(MessageSpec(embeds=(card(footer_text=f"{FOOTER} · {CART}"),))) == ["I1"]
    button = ButtonSpec(label=f"Start {CART} order", custom_id="sb:x:a")
    assert rules(MessageSpec(embeds=(card(),), rows=((button,),))) == ["I1"]
    menu = SelectSpec(custom_id="sb:x:m", options=(SelectOptionSpec(label="A", value="a"),), placeholder=f"{CART} Pick")
    assert rules(MessageSpec(embeds=(card(),), rows=((menu,),))) == ["I1"]
    form = ModalSpec(
        custom_id="sb:x:f", title=f"{CART} Address", inputs=(TextInputSpec(custom_id="a", label="Street"),)
    )
    assert rules(form) == ["I1"]
    # Field names and descriptions show icons; a leading label icon becomes the button's emoji.
    ok = MessageSpec(
        embeds=(card(fields=(EmbedField(name=f"{CART} ITEMS", value=f"{CART} Mug"),)),),
        rows=((ButtonSpec(label=f"{CART} Cart", custom_id="sb:x:a"),),),
    )
    assert rules(ok) == []


def test_i2_a_button_label_always_has_the_same_icon() -> None:
    with_icon = MessageSpec(embeds=(card(),), rows=((ButtonSpec(label=f"{CART} Catalog", custom_id="sb:x:a"),),))
    without = MessageSpec(embeds=(card(),), rows=((ButtonSpec(label="Catalog", custom_id="sb:x:b"),),))
    assert rules(with_icon, with_icon) == []
    assert rules(with_icon, without) == ["I2"]


def test_l1_l2_l5_layout() -> None:
    assert rules(MessageSpec(embeds=(card(), card(), card()))) == ["L1"]
    assert rules(MessageSpec(embeds=(card(title="x" * 61),))) == ["L2"]
    assert rules(MessageSpec(embeds=(card(title="Your cart."),))) == ["L2"]
    grid = tuple(EmbedField(name=f"n{i}", value="v", inline=True) for i in range(7))
    assert rules(MessageSpec(embeds=(card(fields=grid),))) == ["L5"]
    assert rules(MessageSpec(embeds=(card(fields=grid[:6]),))) == []


def test_w1_walls_of_text_but_not_what_people_typed() -> None:
    assert rules(MessageSpec(embeds=(card(description=("word " * 90).strip()),))) == ["W1"]
    typed = "> " + ("[sample review comment] " * 30).strip()
    assert rules(MessageSpec(embeds=(card(description=typed),))) == []


def test_w3_internal_words() -> None:
    assert rules(MessageSpec(embeds=(card(description="Status: pending_payment"),))) == ["W3"]
    assert rules(MessageSpec(embeds=(card(description="Reason: None"),))) == ["W3"]
    # Icons, links and code spans are not words customers read.
    fine = "<:dot_blue:111111111111111111> See https://example.com/a_b or `some_code`."
    assert rules(MessageSpec(embeds=(card(description=fine),))) == []


def test_w4_leftovers_and_spacing() -> None:
    for bad in ("Hi {user_name}", "{icon_cart} Cart", "Double  space", "Trailing \nline", "Space before ,", "⟦x.y⟧"):
        assert rules(MessageSpec(embeds=(card(description=bad),))) == ["W4"], bad
    assert rules(MessageSpec(embeds=(card(description="\nStarts empty"),))) == ["W4"]


def test_operator_replies_get_only_the_icon_and_leftover_rules() -> None:
    operator = EmbedSpec(title="x" * 80, description="Status: pending_payment  done", timestamp=1)
    assert rules(MessageSpec(embeds=(operator,))) == []
    assert rules(MessageSpec(embeds=(EmbedSpec(title="Saved", description="Hi {name}", timestamp=1),))) == ["W4"]


def test_b1_button_length() -> None:
    long = ButtonSpec(label="Open the private order ticket now", custom_id="sb:x:a")
    assert rules(MessageSpec(embeds=(card(),), rows=((long,),))) == ["B1"]


def test_synthetic_stress_states_only_get_the_icon_rules() -> None:
    spec = MessageSpec(embeds=(card(description="[how_to_buy.step.1.name]  " + "word " * 100),))
    assert rules(spec, name="example_longest_message") == []
    assert rules(MessageSpec(embeds=(card(title=f"{CART} x"),)), name="example_longest_message") == ["I1"]


async def test_the_shipped_texts_follow_the_house_style() -> None:
    """Every preview state built with the shipped configuration (config/) passes every [checked] rule, as
    ``python -m tools.check_quality --strict`` does in scripts/quality.sh."""
    states, footer = await collect(ROOT / "config", None)
    assert {"checkout", "problems", "tickets"} <= {state.feature for state in states}
    findings = check_states(states, footer)
    assert findings == [], "\n".join(f"{f.rule} [{f.feature}] {f.state} · {f.where}: {f.message}" for f in findings)
