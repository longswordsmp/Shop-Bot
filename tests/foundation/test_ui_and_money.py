from __future__ import annotations

import discord
import pytest

from shopbot.core.money import MoneyError, currency_exponent, format_amount, parse_amount, percent_of
from shopbot.ui import custom_ids
from shopbot.ui.discord_render import RenderError, send_kwargs, to_modal, to_view
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.routing import RoutedItem, parse_modal_fields
from shopbot.ui.spec import (
    ButtonSpec,
    EmbedField,
    EmbedSpec,
    MessageSpec,
    ModalSpec,
    SelectOptionSpec,
    SelectSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import chunk_lines


def test_custom_id_roundtrip_and_validation() -> None:
    value = custom_ids.cid("tk", "close", 42)
    assert value == "sb:tk:close:42"
    assert custom_ids.parse(value) == ("tk", "close", ("42",))
    assert custom_ids.parse("sb:tk:close") == ("tk", "close", ())
    assert custom_ids.parse("other:thing") is None
    with pytest.raises(custom_ids.CustomIdError):
        custom_ids.cid("TK", "close")
    with pytest.raises(custom_ids.CustomIdError):
        custom_ids.cid("tk", "close", "a b")
    with pytest.raises(custom_ids.CustomIdError):
        custom_ids.cid("tk", "x", *(["a" * 60] * 2))


def test_message_without_embed_is_rejected() -> None:
    violations = validate_message(MessageSpec())
    assert any("at least one embed" in v.message for v in violations)


def test_embed_limits_are_reported_not_truncated() -> None:
    long_title = "t" * 257
    spec = MessageSpec(embeds=(EmbedSpec(title=long_title, description="d"),))
    violations = validate_message(spec)
    assert any("title" in v.path for v in violations)
    with pytest.raises(RenderError):
        send_kwargs(spec)
    assert spec.embeds[0].title == long_title


def test_total_embed_characters_limit() -> None:
    embeds = tuple(EmbedSpec(description="x" * 4000) for _ in range(2))
    violations = validate_message(MessageSpec(embeds=embeds))
    assert any("combined embed text" in v.message for v in violations)


def test_field_limits_and_blank_fields() -> None:
    fields = (*tuple(EmbedField(name=f"n{i}", value="v") for i in range(26)), EmbedField(name=" ", value="v"))
    violations = validate_message(MessageSpec(embeds=(EmbedSpec(title="t", fields=fields),)))
    assert any("27 fields" in v.message for v in violations)
    assert any("field name is blank" in v.message for v in violations)


def test_component_limits() -> None:
    buttons = tuple(ButtonSpec(label=f"b{i}", custom_id=f"sb:x:y:{i}") for i in range(6))
    select = SelectSpec(
        custom_id="sb:x:s", options=tuple(SelectOptionSpec(label=str(i), value=str(i)) for i in range(26))
    )
    spec = MessageSpec(
        embeds=(EmbedSpec(title="t"),), rows=(buttons, (select, ButtonSpec(label="z", custom_id="sb:x:z")))
    )
    messages = [v.message for v in validate_message(spec)]
    assert any("6 buttons" in m for m in messages)
    assert any("26 options" in m for m in messages)
    assert any("must be alone" in m for m in messages)


def test_duplicate_custom_ids_and_missing_labels() -> None:
    spec = MessageSpec(
        embeds=(EmbedSpec(title="t"),),
        rows=(
            (
                ButtonSpec(label="a", custom_id="sb:x:a"),
                ButtonSpec(label="b", custom_id="sb:x:a"),
                ButtonSpec(custom_id="sb:x:c"),
            ),
        ),
    )
    messages = [v.message for v in validate_message(spec)]
    assert any("duplicate custom_id" in m for m in messages)
    assert any("neither label nor emoji" in m for m in messages)


def test_rows_of_packs_buttons_and_isolates_selects() -> None:
    b = [ButtonSpec(label=str(i), custom_id=f"sb:x:b:{i}") for i in range(7)]
    s = SelectSpec(custom_id="sb:x:s", options=(SelectOptionSpec(label="a", value="a"),))
    rows = rows_of(*b[:3], s, *b[3:], None)
    assert [len(r) for r in rows] == [3, 1, 4]


def test_modal_limits() -> None:
    spec = ModalSpec(
        custom_id="sb:x:m", title="x" * 46, inputs=tuple(TextInputSpec(custom_id=f"f{i}", label="l") for i in range(6))
    )
    messages = [v.message for v in validate_modal(spec)]
    assert any("exceeds the limit of 45" in m for m in messages)
    assert any("6 inputs" in m for m in messages)


def test_to_view_wraps_interactive_items_in_routed_item() -> None:
    spec = MessageSpec(
        embeds=(EmbedSpec(title="t"),),
        rows=(
            (
                ButtonSpec(label="go", custom_id="sb:tk:open:general", style="primary"),
                ButtonSpec(label="site", style="link", url="https://example.com"),
            ),
            (SelectSpec(custom_id="sb:tk:pick", options=(SelectOptionSpec(label="a", value="a"),)),),
        ),
    )
    view = to_view(spec)
    assert view is not None
    routed = [c for c in view.children if isinstance(c, RoutedItem)]
    assert len(routed) == 2
    links = [c for c in view.children if isinstance(c, discord.ui.Button) and c.url]
    assert len(links) == 1
    assert view.timeout is None


def test_send_kwargs_disables_mentions_except_listed() -> None:
    spec = MessageSpec(embeds=(EmbedSpec(title="t"),), mention_user_ids=(5,))
    kwargs = send_kwargs(spec)
    mentions: discord.AllowedMentions = kwargs["allowed_mentions"]
    assert mentions.everyone is False
    assert [u.id for u in mentions.users] == [5]  # type: ignore[union-attr]
    assert mentions.roles is False
    assert kwargs["content"] == "<@5>"


def test_to_modal_uses_labels_and_parse_fields_roundtrip() -> None:
    modal = to_modal(
        ModalSpec(custom_id="sb:x:m", title="Form", inputs=(TextInputSpec(custom_id="qty", label="Q", style="short"),))
    )
    assert modal.custom_id == "sb:x:m"
    labels = [c for c in modal.children if isinstance(c, discord.ui.Label)]
    assert labels and labels[0].text == "Q"
    data = {
        "components": [
            {"type": 18, "component": {"type": 4, "custom_id": "qty", "value": "3"}},
            {"type": 1, "components": [{"type": 4, "custom_id": "note", "value": ""}]},
        ]
    }
    assert parse_modal_fields(data) == {"qty": "3", "note": ""}


def test_chunk_lines_never_cuts_lines() -> None:
    lines = ["a" * 10, "b" * 10, "c" * 30]
    blocks = chunk_lines(lines, 25)
    assert blocks == ["a" * 10 + "\n" + "b" * 10, "c" * 30]


def test_money_formatting_and_parsing() -> None:
    assert currency_exponent("USD") == 2
    assert currency_exponent("JPY") == 0
    assert currency_exponent("KWD") == 3
    assert currency_exponent("USD", 0) == 0
    assert format_amount(123456, 2) == "1,234.56"
    assert format_amount(5, 2) == "0.05"
    assert format_amount(123456, 2, ",", ".") == "1.234,56"
    assert format_amount(1500, 0) == "1,500"
    assert parse_amount("12.50", 2) == 1250
    assert parse_amount("1,250.5", 2) == 125050
    assert parse_amount("12,50", 2, ",", ".") == 1250
    assert parse_amount("7", 0) == 7
    for bad in ("", "-1", "1.234", "12,34.00", "abc", "1.2.3"):
        with pytest.raises(MoneyError):
            parse_amount(bad, 2)


def test_percent_rounding_half_up() -> None:
    assert percent_of(1000, 250) == 25
    assert percent_of(999, 50) == 5  # 4.995 -> 5
    assert percent_of(1, 4999) == 0
    assert percent_of(1, 5000) == 1
