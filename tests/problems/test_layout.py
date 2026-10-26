"""Layout of the Report a Problem cards: the answers card under the report panel (heading, each question in bold with
the answer under it, long answers continued within Discord's limits without cutting anything), the key facts of the
decision message, the replies that drop a ticket link they cannot show, and the staff replies (title over details)."""

from __future__ import annotations

import re
from pathlib import Path

from discord.utils import escape_markdown

from shopbot.config.settings import ProblemType
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import views
from shopbot.ui.limits import EMBED_DESCRIPTION, EMBED_TOTAL_CHARS, validate_message
from shopbot.ui.spec import MessageSpec
from tests.helpers import TestEnv
from tests.problems.util import MULTI_TYPES, make_env

# Synthetic answers: markdown that must be escaped, and one long paragraph with words to break between.
STARRED = "*fragile* box_ok"
WORDS = " ".join(f"word{i}*" for i in range(1, 600))  # about 4000 characters, one line


def _billing(tenv: TestEnv) -> ProblemType:
    problem_type = tenv.app.settings.problems.type("billing")
    assert problem_type is not None
    return problem_type


def _question(tenv: TestEnv, field_id: str) -> str:
    return tenv.text(k.form_field_key("billing", field_id, "label"))


def _descriptions(messages: list[MessageSpec]) -> list[str]:
    return [embed.description or "" for message in messages for embed in message.embeds]


async def test_answers_card_shows_each_question_in_bold_over_its_answer(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, MULTI_TYPES)
    try:
        messages = views.details_messages(tenv.app.ui, _billing(tenv), {"what": STARRED, "extra": "second line"})
        assert len(messages) == 1 and len(messages[0].embeds) == 1
        card = messages[0].embeds[0]
        heading = tenv.text(k.DETAILS_HEADING)
        assert card.description == (
            f"**{heading}**\n**{_question(tenv, 'what')}**\n{escape_markdown(STARRED)}\n\n"
            f"**{_question(tenv, 'extra')}**\nsecond line"
        )
        # A card, not a grid of fields; it continues the panel above: no banner, author line or footer.
        assert card.fields == () and card.author_name is None and card.footer_text is None
        assert messages[0].banner is None and messages[0].rows == ()
        # An empty optional answer is left out (no question without an answer).
        only = views.details_messages(tenv.app.ui, _billing(tenv), {"what": "x", "extra": "  "})
        assert _question(tenv, "extra") not in _descriptions(only)[0]
    finally:
        await tenv.close()


async def test_answers_card_without_a_heading_starts_with_the_first_question(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, MULTI_TYPES, content={k.DETAILS_HEADING: ""})
    try:
        messages = views.details_messages(tenv.app.ui, _billing(tenv), {"what": "x"})
        assert _descriptions(messages) == [f"**{_question(tenv, 'what')}**\nx"]
        assert views.details_messages(tenv.app.ui, _billing(tenv), {}) == []  # nothing answered: no card
    finally:
        await tenv.close()


async def test_long_answers_continue_between_words_and_nothing_is_cut(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, MULTI_TYPES)
    try:
        answers = {"what": STARRED, "extra": WORDS}
        messages = views.details_messages(tenv.app.ui, _billing(tenv), answers)
        texts = _descriptions(messages)
        assert len(texts) > 1  # the escaped answer is longer than one embed
        for message in messages:
            assert validate_message(message) == []
            assert len(message.embeds) <= 2  # one card per message (a long card may take a second embed)
            assert sum(embed.text_length() for embed in message.embeds) <= EMBED_TOTAL_CHARS
        assert all(len(text) <= EMBED_DESCRIPTION for text in texts)
        # Each embed ends between two words: never inside a word, never between a backslash and what it escapes.
        for text in texts[:-1]:
            assert re.search(r"word\d+\\\*$", text), text[-20:]
        # Joined at the breaks (one space each), the card holds every character of the escaped answers.
        whole = " ".join(texts)
        assert escape_markdown(WORDS) in whole and escape_markdown(STARRED) in whole
        assert whole.count(f"**{_question(tenv, 'extra')}**") == 1  # a continuation does not repeat the question
    finally:
        await tenv.close()


async def test_an_answer_without_spaces_is_split_exactly_and_kept_whole(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, MULTI_TYPES)
    try:
        solid = "*" * 4000  # escaped: 8000 characters with no space or line break to split at
        messages = views.details_messages(tenv.app.ui, _billing(tenv), {"what": "x", "extra": solid})
        texts = _descriptions(messages)
        assert all(len(text) <= EMBED_DESCRIPTION for text in texts)
        assert all(not text.endswith("\\") for text in texts)  # an escape is never separated from its character
        assert "".join(texts).count("\\*") == 4000
    finally:
        await tenv.close()


async def test_decision_message_shows_the_same_key_facts_as_the_panel(pr: TestEnv) -> None:
    order = views.OrderInfo(order_id=1, number="1001", total="$5.00", status="Paid")
    spec = views.decision_notification(
        pr.app.ui,
        pr.app.settings.problems,
        status=k.REJECTED,
        number="0001",
        type_id="general",
        order=order,
        reason="not *ours*",
        channel_id=42,
    )
    fields = spec.embeds[0].fields
    # The short facts side by side, the order full width under them (never squeezed into a third of the card).
    assert [(f.name, f.inline) for f in fields] == [
        (pr.text(k.FIELD_TYPE), True),
        (pr.text(k.FIELD_STATUS), True),
        (pr.text(k.FIELD_ORDER), False),
        (pr.text(k.FIELD_REASON), False),
    ]
    assert fields[1].value == pr.text(k.status_key(k.REJECTED)) and fields[3].value == r"not \*ours\*"


async def test_lines_linking_a_ticket_that_cannot_be_linked_are_left_out(pr: TestEnv) -> None:
    content = pr.app.content
    content.set_override(k.LIMIT_REACHED, "Limit {limit}.\nContinue in {channel_mention}.")
    assert views.limit_reply(pr.app.ui, limit=1, channel_id=None).embeds[0].description == "Limit 1."
    assert views.limit_reply(pr.app.ui, limit=1, channel_id=7).embeds[0].description == "Limit 1.\nContinue in <#7>."
    content.set_override(k.notify_key(k.RESOLVED, "description"), "Resolved.\nTicket: {channel_mention}")
    spec = views.decision_notification(
        pr.app.ui,
        pr.app.settings.problems,
        status=k.RESOLVED,
        number="0001",
        type_id="general",
        order=None,
        reason=None,
        channel_id=None,
    )
    assert spec.embeds[0].description == "Resolved."


async def test_replies_carry_their_optional_titles(pr: TestEnv) -> None:
    created = views.created_reply(pr.app.ui, channel_id=7, number="0001")
    assert created.embeds[0].title == pr.app.content.render(k.CREATED_TITLE, number="0001")
    limit = views.limit_reply(pr.app.ui, limit=1, channel_id=7)
    assert limit.embeds[0].title == pr.text(k.LIMIT_REACHED_TITLE)


async def test_staff_reply_has_a_title_over_its_details(pr: TestEnv) -> None:
    spec = views.operator_reply(pr.app.ui, "Report #0001 resolved", "Customer notification: sent.", tone="success")
    assert (spec.embeds[0].title, spec.embeds[0].description) == (
        "Report #0001 resolved",
        "Customer notification: sent.",
    )
    assert spec.banner == pr.app.ui.banner("general-response")
