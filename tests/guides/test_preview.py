"""Preview states of the guides: every state is within Discord's limits and nothing in it is unrouted."""

from __future__ import annotations

from pathlib import Path

import pytest

from shopbot.features.guides import FAQ_MODULE, HOW_TO_BUY_MODULE, MODULES, SHIPPING_MODULE, faq, how_to_buy
from shopbot.features.server_keys import channel_url
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import MessageSpec, PreviewState
from tests.guides.util import guide_env
from tests.helpers import TestEnv
from tools.check_limits import unrouted_components


def states_of(tenv: TestEnv) -> list[PreviewState]:
    states: list[PreviewState] = []
    for module in MODULES:
        assert module.preview is not None
        states += module.preview(tenv.app)
    return states


def assert_states_valid(tenv: TestEnv, states: list[PreviewState]) -> None:
    names = [(s.feature, s.name) for s in states]
    assert len(names) == len(set(names))
    for preview_state in states:
        assert isinstance(preview_state.spec, MessageSpec)
        assert validate_message(preview_state.spec) == [], preview_state.name
        assert unrouted_components(preview_state, tenv.app.router) == [], preview_state.name
        assert preview_state.note


async def test_configured_previews_use_the_runtime_views(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path)
    try:
        states = states_of(tenv)
        assert_states_valid(tenv, states)
        by_name = {(s.feature, s.name): s.spec for s in states}
        guide = by_name[("how_to_buy", "guide")]
        assert isinstance(guide, MessageSpec)
        assert guide.embeds[0].title == tenv.text(how_to_buy.TITLE)
        assert [b.url for row in guide.rows for b in row] == [
            channel_url(tenv.app, key) for key in ("catalog", "purchase", "faq")
        ]
        faq_spec = by_name[("faq", "faq")]
        assert isinstance(faq_spec, MessageSpec)
        assert len(faq_spec.embeds) == 1  # one card
        assert all(
            f"**{tenv.text(faq.section_title_key(s))}**" in (faq_spec.embeds[0].description or "")
            for s in ("general", "orders")
        )
        assert ("shipping", "shipping") in by_name
    finally:
        await tenv.close()


async def test_previews_include_multi_message_examples(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path)
    try:
        names = {s.name for s in states_of(tenv)}
        assert {"example_many_sections_message_1", "example_many_sections_message_2"} <= names
        assert {"example_long_section_message_1", "example_long_section_message_2"} <= names
        assert {"example_countries_message_1", "example_countries_message_2"} <= names
        assert {"example_longest_message_1", "example_longest_message_2"} <= names
        many = [s for s in states_of(tenv) if s.name.startswith("example_many_sections")]
        assert all("Synthetic example" in s.note for s in many)
        long_section = [s.spec for s in states_of(tenv) if s.name.startswith("example_long_section")]
        assert any(isinstance(s, MessageSpec) and s.embeds[0].title is None for s in long_section[1:])
        longest = [s.spec for s in states_of(tenv) if s.name.startswith("example_longest")]
        assert isinstance(longest[-1], MessageSpec) and len(longest[-1].rows) == 5  # 25 link buttons, last message
    finally:
        await tenv.close()


async def test_preview_without_text_shows_markers_instead_of_inventing_wording(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, provision=False)
    tenv.app.content.replace(file_values={})
    tenv.app.recompute_readiness()
    try:
        states = states_of(tenv)
        assert_states_valid(tenv, states)
        guide = next(s.spec for s in states if s.name == "guide")
        assert isinstance(guide, MessageSpec)
        assert guide.embeds[0].title == f"[{how_to_buy.TITLE}]"
        assert f"**[{how_to_buy.step_name_key(1)}]**" in (guide.embeds[0].description or "")
        assert next(b.url for row in guide.rows for b in row) == "https://example.com/channel/catalog"
    finally:
        await tenv.close()


async def test_preview_marks_channel_links_before_setup(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, provision=False)
    try:
        guide = next(s.spec for s in states_of(tenv) if s.name == "guide")
        assert isinstance(guide, MessageSpec)
        assert "<#0>" in (guide.embeds[0].description or "")  # shown as #channel in the HTML preview
        assert "{channel_" not in (guide.embeds[0].description or "")
    finally:
        await tenv.close()


@pytest.mark.parametrize("module", [HOW_TO_BUY_MODULE, FAQ_MODULE, SHIPPING_MODULE])
async def test_preview_works_with_the_unconfigured_base_layout(tenv: TestEnv, module: object) -> None:
    preview = getattr(module, "preview")  # noqa: B009
    states = preview(tenv.app)
    assert states
    assert_states_valid(tenv, states)


def test_modules_are_registered_with_their_prefixes() -> None:
    from shopbot.features import all_modules

    names = [m.name for m in all_modules()]
    assert {"how_to_buy", "faq", "shipping"} <= set(names)
    assert names.index("core") < names.index("how_to_buy")
    prefixes = {m.name: m.panel_prefixes for m in MODULES}
    assert prefixes == {"how_to_buy": ("how_to_buy:",), "faq": ("faq:",), "shipping": ("shipping:",)}
    assert all(m.publish is not None and m.preview is not None for m in MODULES)
