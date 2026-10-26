"""Layout of the ticket cards: the status cards posted in a ticket carry a plain title over their status line (the title
is optional), the close and delete prompts confirm with the action's own button, and no card title of the shipped
support texts holds an icon (Discord shows icons there as raw text)."""

from __future__ import annotations

from pathlib import Path

from shopbot.config.content_keys import Kind
from shopbot.config.loader import load_config
from shopbot.config.readiness import build_registry
from shopbot.features import all_modules
from shopbot.features import core_keys as core
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import views
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import ButtonSpec, MessageSpec
from tests.helpers import ROOT, TestEnv
from tests.tickets.util import make_env

USER_ID = 1
STAFF_ID = 2
CHANNEL_ID = 3
TITLES = (
    k.CLAIMED_TITLE,
    k.UNCLAIMED_TITLE,
    k.REOPENED_TITLE,
    k.DELETION_SCHEDULED_TITLE,
    k.AUTO_CLOSE_WARNING_TITLE,
    k.USER_ADDED_TITLE,
    k.USER_REMOVED_TITLE,
    k.CREATED_TITLE,
)


def _cards(tenv: TestEnv) -> list[tuple[MessageSpec, str, str]]:
    """(card, title key, expected text) of every status card and the created reply."""
    ui, render = tenv.app.ui, tenv.app.content.render
    number = views.number_text(tenv.app.settings.tickets, 1)
    cases = [
        (views.claimed(ui, STAFF_ID), k.CLAIMED_TITLE, render(k.CLAIMED, staff_mention=f"<@{STAFF_ID}>")),
        (views.unclaimed(ui), k.UNCLAIMED_TITLE, render(k.UNCLAIMED)),
        (views.reopened(ui, STAFF_ID), k.REOPENED_TITLE, render(k.REOPENED, actor_mention=f"<@{STAFF_ID}>")),
        (views.deletion_scheduled(ui, 5), k.DELETION_SCHEDULED_TITLE, render(k.DELETION_SCHEDULED, minutes=5)),
        (
            views.auto_close_warning(ui, minutes=10, user_id=USER_ID),
            k.AUTO_CLOSE_WARNING_TITLE,
            render(k.AUTO_CLOSE_WARNING, minutes=10, user_mention=f"<@{USER_ID}>"),
        ),
        (views.user_added(ui, USER_ID), k.USER_ADDED_TITLE, render(k.USER_ADDED, user_mention=f"<@{USER_ID}>")),
        (views.user_removed(ui, USER_ID), k.USER_REMOVED_TITLE, render(k.USER_REMOVED, user_mention=f"<@{USER_ID}>")),
        (
            views.created_reply(ui, tenv.app.settings.tickets, channel_id=CHANNEL_ID, number=1),
            k.CREATED_TITLE,
            render(k.CREATED, channel_mention=f"<#{CHANNEL_ID}>", number=number),
        ),
    ]
    return [(spec, title_key, str(text)) for spec, title_key, text in cases]


async def test_status_cards_have_a_title_over_their_status_line(tk: TestEnv) -> None:
    number = views.number_text(tk.app.settings.tickets, 1)
    for spec, title_key, text in _cards(tk):
        (embed,) = spec.embeds
        assert embed.title == tk.app.content.render(title_key, number=number), title_key
        assert embed.description == text, title_key


async def test_status_cards_without_a_title_still_show_their_text(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content=dict.fromkeys(TITLES, ""))
    try:
        for spec, title_key, text in _cards(tenv):
            assert spec.embeds[0].title is None and spec.embeds[0].description == text, title_key
    finally:
        await tenv.close()


def _buttons(spec: MessageSpec) -> list[tuple[str | None, str | None, str]]:
    return [(b.label, b.custom_id, b.style) for row in spec.rows for b in row if isinstance(b, ButtonSpec)]


async def test_close_and_delete_prompts_confirm_with_the_action_button(tk: TestEnv) -> None:
    cancel = tk.text(core.BUTTON_CANCEL)
    assert _buttons(views.close_confirm(tk.app.ui, 7)) == [
        (tk.text(k.BUTTON_CLOSE), cid("tk", "closeok", 7), "danger"),
        (cancel, cid("tk", "closeno", 7), "secondary"),
    ]
    assert _buttons(views.delete_confirm(tk.app.ui, 7)) == [
        (tk.text(k.BUTTON_DELETE), cid("tk", "delok", 7), "danger"),
        (cancel, cid("tk", "delno", 7), "secondary"),
    ]


def test_shipped_support_card_titles_have_no_icons() -> None:
    loaded = load_config(ROOT / "config")
    registry = build_registry(loaded.settings, all_modules())
    titles = [
        key.key
        for feature in ("tickets", "problems")
        for key in registry.for_feature(feature)
        if key.kind is Kind.TITLE and key.key.endswith(".title")  # ``*.heading`` keys are section headings
    ]
    assert k.PANEL_TITLE in titles and k.STAFF_ALERT_TITLE in titles and k.CLAIMED_TITLE in titles
    with_icons = [key for key in titles if "{icon_" in (loaded.content_values.get(key) or "")]
    assert with_icons == []
