"""Content keys, settings checks, readiness, operator text, preview states and Discord limits."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from shopbot.config.content_keys import ContentKey, Kind
from shopbot.features import core_keys as ck
from shopbot.features.reviews import MODULE, content_keys, repo, schedule_review_prompt, service
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews.text import OPERATOR_TEXT
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec, ModalSpec, SelectSpec
from tests.helpers import TestEnv, make_settings, make_test_env, shipped_settings_dict
from tests.reviews.util import number_text, rendered
from tools.check_limits import unrouted_components

PACKAGE = Path(__file__).resolve().parents[2] / "shopbot" / "features" / "reviews"
FIXED_KEYS = {
    k.PROMPT_TITLE,
    k.PROMPT_DESCRIPTION,
    k.PROMPT_SELECT_PLACEHOLDER,
    k.BUTTON_DECLINE,
    k.COMMENT_TITLE,
    k.COMMENT_LABEL,
    k.COMMENT_DESCRIPTION,
    k.COMMENT_PLACEHOLDER,
    k.PUBLISHED_TITLE,
    k.PUBLISHED_RATING_FIELD,
    k.PUBLISHED_VERIFIED_FIELD,
    k.PUBLISHED_VERIFIED_VALUE,
    k.THANKS,
    k.THANKS_PENDING,
    k.DECLINED,
    k.ALREADY_REVIEWED,
    k.EXPIRED,
    # staff approval message (reviews.require_approval)
    k.APPROVAL_TITLE,
    k.APPROVAL_RATING_FIELD,
    k.APPROVAL_ORDER_FIELD,
    k.APPROVAL_CUSTOMER_FIELD,
    k.APPROVAL_STATUS_FIELD,
    k.APPROVAL_REASON_FIELD,
    k.APPROVAL_BUTTON_APPROVE,
    k.APPROVAL_BUTTON_REJECT,
    *(k.approval_status_key(state) for state in k.APPROVAL_STATES),
    # decision notices
    *(k.notice_key(decision, part) for decision in k.NOTICE_DECISIONS for part in ("title", "description")),
    k.NOTICE_REJECTED_REASON_FIELD,
}


def _keys(overrides: dict[str, Any] | None = None) -> dict[str, ContentKey]:
    return {d.key: d for d in content_keys.content_keys(make_settings(overrides))}


# --------------------------------------------------------------------------------------------------
# Content keys
# --------------------------------------------------------------------------------------------------


def test_content_keys_per_rating_scale() -> None:
    keys = _keys()
    rating_keys = {k.rating_key(n, part) for n in range(1, 6) for part in ("label", "description", "emoji", "display")}
    assert set(keys) == FIXED_KEYS | rating_keys
    for definition in keys.values():
        assert definition.feature == k.REVIEWS and definition.group and definition.doc
    assert set(_keys({"reviews": {"rating_scale": 10}})) == FIXED_KEYS | {
        k.rating_key(n, part) for n in range(1, 11) for part in ("label", "description", "emoji", "display")
    }
    # an unset scale (business decision missing) declares no rating keys
    assert set(_keys({"reviews": {"rating_scale": 0}})) == FIXED_KEYS


def test_kinds_required_flags_and_placeholders() -> None:
    keys = _keys()
    expect = {
        k.PROMPT_TITLE: (Kind.TITLE, False, ("order_number",)),
        k.PROMPT_DESCRIPTION: (Kind.DESCRIPTION, True, ("order_number",)),
        k.PROMPT_SELECT_PLACEHOLDER: (Kind.SELECT_PLACEHOLDER, True, ()),
        k.BUTTON_DECLINE: (Kind.BUTTON_LABEL, True, ()),
        k.COMMENT_TITLE: (Kind.MODAL_TITLE, True, ()),
        k.COMMENT_LABEL: (Kind.INPUT_LABEL, True, ()),
        k.COMMENT_DESCRIPTION: (Kind.INPUT_DESCRIPTION, False, ()),
        k.COMMENT_PLACEHOLDER: (Kind.INPUT_PLACEHOLDER, False, ()),
        k.PUBLISHED_TITLE: (Kind.TITLE, True, ("rating", "user_name", "order_number")),
        k.PUBLISHED_RATING_FIELD: (Kind.FIELD_NAME, True, ()),
        k.THANKS: (Kind.DESCRIPTION, True, ("order_number",)),
        k.DECLINED: (Kind.DESCRIPTION, True, ("order_number",)),
        k.ALREADY_REVIEWED: (Kind.DESCRIPTION, True, ("order_number",)),
        k.EXPIRED: (Kind.DESCRIPTION, True, ("order_number",)),
        k.rating_key(3, "label"): (Kind.OPTION_LABEL, True, ()),
        k.rating_key(3, "description"): (Kind.OPTION_DESCRIPTION, False, ()),
        k.rating_key(3, "emoji"): (Kind.EMOJI, False, ()),
        k.rating_key(3, "display"): (Kind.FIELD_VALUE, False, ()),
    }
    for key, (kind, required, placeholders) in expect.items():
        assert (keys[key].kind, keys[key].required, keys[key].placeholders) == (kind, required, placeholders), key
    assert keys[k.rating_key(3, "display")].fallback == k.rating_key(3, "label")


def test_comment_keys_required_only_when_comments_are_allowed() -> None:
    keys = _keys({"reviews": {"allow_comment": False}})
    assert not keys[k.COMMENT_TITLE].required and not keys[k.COMMENT_LABEL].required


def test_every_content_key_used_in_code_is_declared() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    constants = set(
        re.findall(
            r'= "(reviews\.(?:prompt|button|comment|published|thanks|thanks_pending|declined|already_reviewed|expired|'
            r'approval|notice)\.[a-z_.]+)"',
            source,
        )
    )
    generated = {k.approval_status_key(state) for state in k.APPROVAL_STATES} | {
        k.notice_key(decision, part) for decision in k.NOTICE_DECISIONS for part in ("title", "description")
    }
    assert constants == FIXED_KEYS - generated


# --------------------------------------------------------------------------------------------------
# Settings and readiness
# --------------------------------------------------------------------------------------------------


def test_settings_problems() -> None:
    assert MODULE.settings_problems(make_settings()) == []
    shipped = make_settings({"features": {"reviews": True}}, all_features=False)
    problems = MODULE.settings_problems(shipped)
    assert any("reviews.prompt_delay_minutes is not set" in p for p in problems)
    assert any("reviews.rating_scale is not set" in p for p in problems)
    assert len(problems) == 2, problems
    undefined = MODULE.settings_problems(make_settings({"reviews": {"channel": "nowhere"}}))
    assert undefined == ["reviews.channel: channel 'nowhere' is not defined in server.channels"]
    assert any(
        "reviews.channel: required" in p for p in MODULE.settings_problems(make_settings({"reviews": {"channel": ""}}))
    )
    dm_only = make_settings({"reviews": {"prompt_via": "dm"}, "features": {"dm_notifications": False}})
    assert any("features.dm_notifications" in p for p in MODULE.settings_problems(dm_only))
    assert MODULE.settings_problems(make_settings({"reviews": {"prompt_via": "dm"}})) == []
    contradiction = make_settings({"reviews": {"allow_comment": False, "comment_required": True}})
    assert any("allow_comment" in p for p in MODULE.settings_problems(contradiction))


def test_module_contract() -> None:
    assert MODULE.name == "reviews"
    assert MODULE.depends_on == ("checkout",)
    assert MODULE.is_enabled(make_settings()) is True
    assert MODULE.is_enabled(make_settings({"features": {"reviews": False}})) is False
    assert shipped_settings_dict()["reviews"]["rating_scale"] == 0  # the rating scale is a business decision


async def test_ready_with_full_content(tenv: TestEnv) -> None:
    assert tenv.app.ready(k.REVIEWS), tenv.app.readiness.statuses[k.REVIEWS].problems


async def test_missing_required_content_keeps_feature_not_ready(tmp_path: Path) -> None:
    for key in (k.rating_key(3, "label"), k.THANKS, k.PUBLISHED_TITLE, k.COMMENT_LABEL):
        tenv = await make_test_env(tmp_path / key, content={key: ""})
        try:
            status = tenv.app.readiness.statuses[k.REVIEWS]
            assert not status.ready
            assert any(p.kind == "missing_content" and p.key == key for p in status.problems)
            # scheduling is a no-op while not ready
            await schedule_review_prompt(tenv.app, 1)
            assert await tenv.app.db.run(lambda c: c.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0]) == 0
        finally:
            await tenv.close()


async def test_optional_keys_do_not_block_readiness(tmp_path: Path) -> None:
    optional = {
        k.PROMPT_TITLE: "",
        k.COMMENT_DESCRIPTION: "",
        k.COMMENT_PLACEHOLDER: "",
        k.rating_key(1, "description"): "",
        k.rating_key(1, "emoji"): "",
        k.rating_key(1, "display"): "",
    }
    tenv = await make_test_env(tmp_path, content=optional)
    try:
        assert tenv.app.ready(k.REVIEWS)
    finally:
        await tenv.close()


async def test_comment_keys_optional_without_comments(tmp_path: Path) -> None:
    settings = make_settings({"reviews": {"allow_comment": False}})
    tenv = await make_test_env(tmp_path, settings=settings, content={k.COMMENT_TITLE: "", k.COMMENT_LABEL: ""})
    try:
        assert tenv.app.ready(k.REVIEWS)
    finally:
        await tenv.close()


async def test_readiness_requires_checkout(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"features": {"checkout": False}}))
    try:
        status = tenv.app.readiness.statuses[k.REVIEWS]
        assert not status.ready
        assert any(p.kind == "dependency" and "checkout" in p.message for p in status.problems)
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Operator text
# --------------------------------------------------------------------------------------------------


def test_operator_text_keys_are_prefixed_and_used() -> None:
    assert all(key.startswith("reviews.") for key in OPERATOR_TEXT)
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    used = set(re.findall(r'"(reviews\.[a-z_.]+)"', source))
    dynamic = {f"reviews.status.{status}" for status in repo.STATUSES} | {
        f"reviews.decision.notice.{notice}" for notice in ("dm", "channel", "none", "duplicate", "off")
    }
    content_key_names = FIXED_KEYS
    identifiers = {
        service.PROMPT_TASK,
        service.PUBLISH_TASK,
        service.APPROVAL_TASK,
        service.DECISION_TASK,
        service.APPROVED_KIND,
        service.REJECTED_KIND,
        "reviews.prompted",
        "reviews.published",
        "reviews.declined",
        "reviews.removed",
        "reviews.resent",
        "reviews.prompt_cancelled",
        "reviews.approved",
        "reviews.rejected",
        "reviews.channel",  # settings paths in settings_problems
        "reviews.approval_channel",
        "reviews.approval",  # content key prefix used in a docstring of content_keys
        "reviews.thanks_pending.description",
    }
    operator_used = (used - content_key_names - identifiers) | dynamic
    assert operator_used <= set(OPERATOR_TEXT), operator_used - set(OPERATOR_TEXT)
    assert set(OPERATOR_TEXT) <= operator_used, set(OPERATOR_TEXT) - operator_used


# --------------------------------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------------------------------

ALL_STATES = {
    "prompt",
    "published_with_comment",
    "published_longest_comment",
    "published_without_comment",
    "approval_request",
    "approval_request_longest_comment",
    "approval_request_without_comment",
    "approval_approved",
    "approval_rejected",
    "approval_removed",
    "comment_form",
    "thanks",
    "thanks_pending_approval",
    "declined",
    "already_reviewed",
    "expired",
    "notice_approved",
    "notice_rejected",
    "staff_reject_form",
    "staff_decision_reply",
    "staff_list",
    "staff_list_empty",
}


async def test_every_preview_state_passes_discord_limits(tenv: TestEnv) -> None:
    assert MODULE.preview is not None
    states = MODULE.preview(tenv.app)
    assert {s.name for s in states} == ALL_STATES
    for state in states:
        assert state.feature == k.REVIEWS and state.note
        if isinstance(state.spec, ModalSpec):
            assert validate_modal(state.spec) == [], state.name
        else:
            assert isinstance(state.spec, MessageSpec)
            assert validate_message(state.spec) == [], state.name
        assert unrouted_components(state, tenv.app.router) == [], state.name


async def test_preview_with_largest_scale_and_longest_comment(tmp_path: Path) -> None:
    settings = make_settings({"reviews": {"rating_scale": 10, "comment_max_length": 4000}})
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        assert MODULE.preview is not None
        states = {s.name: s.spec for s in MODULE.preview(tenv.app)}
        prompt = states["prompt"]
        assert isinstance(prompt, MessageSpec)
        select = prompt.rows[0][0]
        assert isinstance(select, SelectSpec) and len(select.options) == 10
        longest = states["published_longest_comment"]
        assert isinstance(longest, MessageSpec)
        assert len(longest.embeds[0].description or "") >= 4000
        for spec in states.values():
            assert (validate_modal(spec) if isinstance(spec, ModalSpec) else validate_message(spec)) == []
    finally:
        await tenv.close()


async def test_preview_uses_exact_configured_text(tenv: TestEnv) -> None:
    assert MODULE.preview is not None
    states = {s.name: s.spec for s in MODULE.preview(tenv.app)}
    number = number_text(tenv, 1)
    prompt = states["prompt"]
    assert isinstance(prompt, MessageSpec)
    embed = prompt.embeds[0]
    assert embed.title == rendered(tenv, k.PROMPT_TITLE, order_number=number)
    assert embed.description == rendered(tenv, k.PROMPT_DESCRIPTION, order_number=number)
    select, decline = prompt.rows[0][0], prompt.rows[1][0]
    assert isinstance(select, SelectSpec)
    assert select.placeholder == tenv.text(k.PROMPT_SELECT_PLACEHOLDER)
    assert [o.value for o in select.options] == ["5", "4", "3", "2", "1"]
    assert [o.label for o in select.options] == [tenv.text(k.rating_key(n, "label")) for n in (5, 4, 3, 2, 1)]
    assert [o.description for o in select.options] == [
        tenv.text(k.rating_key(n, "description")) for n in (5, 4, 3, 2, 1)
    ]
    assert decline.label == tenv.text(k.BUTTON_DECLINE)  # type: ignore[union-attr]
    published = states["published_with_comment"]
    assert isinstance(published, MessageSpec)
    card = published.embeds[0]
    assert card.title == rendered(tenv, k.PUBLISHED_TITLE, rating=5, user_name="[sample member]", order_number=number)
    assert card.description == "[sample review comment]"
    assert [(f.name, f.value) for f in card.fields] == [
        (tenv.text(k.PUBLISHED_RATING_FIELD), tenv.text(k.rating_key(5, "display"))),
        (tenv.text(k.PUBLISHED_VERIFIED_FIELD), rendered(tenv, k.PUBLISHED_VERIFIED_VALUE, order_number=number)),
    ]
    bare = states["published_without_comment"]
    assert isinstance(bare, MessageSpec) and bare.embeds[0].description is None
    modal = states["comment_form"]
    assert isinstance(modal, ModalSpec)
    assert modal.title == tenv.text(k.COMMENT_TITLE)
    (comment,) = modal.inputs
    assert (comment.label, comment.description, comment.placeholder) == (
        tenv.text(k.COMMENT_LABEL),
        tenv.text(k.COMMENT_DESCRIPTION),
        tenv.text(k.COMMENT_PLACEHOLDER),
    )
    assert comment.required is False and comment.max_length == 1000 and comment.style == "paragraph"
    for name, key in (
        ("thanks", k.THANKS),
        ("declined", k.DECLINED),
        ("already_reviewed", k.ALREADY_REVIEWED),
        ("expired", k.EXPIRED),
    ):
        spec = states[name]
        assert isinstance(spec, MessageSpec) and not spec.rows
        assert spec.embeds[0].description == rendered(tenv, key, order_number=number)
        assert spec.embeds[0].title is None  # no invented title


async def test_rating_display_falls_back_to_label(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={k.rating_key(5, "display"): "", k.PROMPT_TITLE: ""})
    try:
        assert MODULE.preview is not None
        states = {s.name: s.spec for s in MODULE.preview(tenv.app)}
        published = states["published_with_comment"]
        assert isinstance(published, MessageSpec)
        assert published.embeds[0].fields[0].value == tenv.text(k.rating_key(5, "label"))
        prompt = states["prompt"]
        assert isinstance(prompt, MessageSpec) and prompt.embeds[0].title is None  # optional title omitted
    finally:
        await tenv.close()


async def test_preview_skips_states_without_configured_text(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, fill_content=False)
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        # operator text only
        assert names == {"staff_list", "staff_list_empty", "staff_reject_form", "staff_decision_reply"}
        assert tenv.app.ui.content.get(ck.ERROR_GENERIC) is None
    finally:
        await tenv.close()


async def test_preview_without_rating_scale(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"reviews": {"rating_scale": 0}}))
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        assert "prompt" not in names and "published_with_comment" not in names
        assert "thanks" in names
    finally:
        await tenv.close()
