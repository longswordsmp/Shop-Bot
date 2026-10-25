"""Settings, content keys, readiness, the migration and the preview states of review approval and the
verification marker."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.content_keys import ContentKey, Kind
from shopbot.config.settings import SettingsError, parse_settings
from shopbot.db.migrate import apply_migrations, discover
from shopbot.features.reviews import MODULE, repo, views
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews.preview import SAMPLE_MEMBER_ID, SAMPLE_REASON
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import ButtonSpec, MessageSpec, ModalSpec
from tests.helpers import TestEnv, base_settings_dict, deep_merge, make_settings, make_test_env, shipped_settings_dict
from tests.reviews.util import number_text, rendered
from tests.reviews_approval.util import approval_overrides, make_approval_env
from tools.check_limits import unrouted_components

APPROVAL_KEYS = {
    k.APPROVAL_TITLE,
    k.APPROVAL_RATING_FIELD,
    k.APPROVAL_ORDER_FIELD,
    k.APPROVAL_CUSTOMER_FIELD,
    k.APPROVAL_STATUS_FIELD,
    k.APPROVAL_BUTTON_APPROVE,
    k.APPROVAL_BUTTON_REJECT,
    *(k.approval_status_key(state) for state in k.APPROVAL_STATES),
}
OPTIONAL_KEYS = {
    k.PUBLISHED_VERIFIED_FIELD,
    k.PUBLISHED_VERIFIED_VALUE,
    k.THANKS_PENDING,
    k.APPROVAL_REASON_FIELD,
    k.NOTICE_REJECTED_REASON_FIELD,
    *(k.notice_key(decision, part) for decision in k.NOTICE_DECISIONS for part in ("title", "description")),
}


def _keys(overrides: dict[str, Any] | None = None) -> dict[str, ContentKey]:
    return {d.key: d for d in k.content_keys(make_settings(overrides))}


# --------------------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------------------


def test_settings_defaults_and_shipped_values() -> None:
    settings = make_settings()
    assert settings.reviews.require_approval is False and settings.reviews.approval_channel == ""
    shipped = shipped_settings_dict()["reviews"]
    assert shipped["require_approval"] is False  # behaviour unchanged unless the owner turns it on
    assert shipped["approval_channel"] == "staff_chat"
    parsed = parse_settings(shipped_settings_dict())
    assert parsed.reviews.approval_channel == "staff_chat"
    assert not any("approval" in p for p in MODULE.settings_problems(parsed))  # off: nothing reported about it


@pytest.mark.parametrize(
    ("reviews", "message"),
    [
        ({"require_approval": "yes"}, "reviews.require_approval: expected true or false"),
        ({"approval_channel": "Staff Chat"}, "reviews.approval_channel"),
        ({"approval_channel": 5}, "reviews.approval_channel: expected a string"),
    ],
)
def test_invalid_settings_are_fatal(reviews: dict[str, Any], message: str) -> None:
    with pytest.raises(SettingsError) as exc:
        parse_settings(deep_merge(base_settings_dict(), {"reviews": reviews}))
    assert any(message in error for error in exc.value.errors), exc.value.errors


def test_settings_problems_when_approval_is_on() -> None:
    assert MODULE.settings_problems(make_settings(approval_overrides())) == []
    no_channel = make_settings(approval_overrides({"reviews": {"approval_channel": ""}}))
    assert any("reviews.approval_channel: required" in p for p in MODULE.settings_problems(no_channel))
    unknown = make_settings(approval_overrides({"reviews": {"approval_channel": "nowhere"}}))
    assert "reviews.approval_channel: channel 'nowhere' is not defined in server.channels" in (
        MODULE.settings_problems(unknown)
    )
    public = make_settings(approval_overrides({"reviews": {"approval_channel": "reviews"}}))
    assert any(
        "reviews.approval_channel: channel 'reviews' must have staff or admin access" in p
        for p in MODULE.settings_problems(public)
    )
    # with approval off the approval channel is not used, so it is not checked
    off = make_settings({"reviews": {"require_approval": False, "approval_channel": "nowhere"}})
    assert MODULE.settings_problems(off) == []


# --------------------------------------------------------------------------------------------------
# Content keys and readiness
# --------------------------------------------------------------------------------------------------


def test_approval_keys_are_required_only_with_approval() -> None:
    off = _keys()
    on = _keys(approval_overrides())
    assert set(off) >= APPROVAL_KEYS | OPTIONAL_KEYS  # always declared (checklist, preview)
    assert all(not off[key].required for key in APPROVAL_KEYS)
    assert all(on[key].required for key in APPROVAL_KEYS)
    assert all(not on[key].required and not off[key].required for key in OPTIONAL_KEYS)
    for definition in on.values():
        assert definition.doc and definition.group and definition.feature == k.REVIEWS


def test_kinds_and_placeholders() -> None:
    keys = _keys(approval_overrides())
    expect = {
        k.PUBLISHED_VERIFIED_FIELD: (Kind.FIELD_NAME, ()),
        k.PUBLISHED_VERIFIED_VALUE: (Kind.FIELD_VALUE, ("order_number",)),
        k.THANKS_PENDING: (Kind.DESCRIPTION, ("order_number",)),
        k.APPROVAL_TITLE: (Kind.TITLE, ("rating", "user_name", "order_number")),
        k.APPROVAL_STATUS_FIELD: (Kind.FIELD_NAME, ()),
        k.approval_status_key(k.STATE_PENDING): (Kind.FIELD_VALUE, ()),
        k.approval_status_key(k.STATE_APPROVED): (Kind.FIELD_VALUE, ("staff_mention",)),
        k.approval_status_key(k.STATE_REJECTED): (Kind.FIELD_VALUE, ("staff_mention",)),
        k.approval_status_key(k.STATE_REMOVED): (Kind.FIELD_VALUE, ()),
        k.APPROVAL_BUTTON_APPROVE: (Kind.BUTTON_LABEL, ()),
        k.notice_key("approved", "description"): (Kind.DESCRIPTION, ("order_number", "review_link")),
        k.notice_key("rejected", "description"): (Kind.DESCRIPTION, ("order_number",)),
        k.NOTICE_REJECTED_REASON_FIELD: (Kind.FIELD_NAME, ()),
    }
    for key, (kind, placeholders) in expect.items():
        assert (keys[key].kind, keys[key].placeholders) == (kind, placeholders), key
    assert keys[k.THANKS_PENDING].fallback == k.THANKS


async def test_missing_approval_text_keeps_reviews_not_ready_only_with_approval(tmp_path: Path) -> None:
    for key in sorted(APPROVAL_KEYS):
        tenv = await make_approval_env(tmp_path / key, content={key: ""})
        try:
            status = tenv.app.readiness.statuses[k.REVIEWS]
            assert not status.ready
            assert any(p.kind == "missing_content" and p.key == key for p in status.problems)
        finally:
            await tenv.close()
    blank = dict.fromkeys(APPROVAL_KEYS | OPTIONAL_KEYS, "")
    tenv = await make_test_env(tmp_path / "off", content=blank)
    try:
        assert tenv.app.ready(k.REVIEWS)  # approval off: none of these texts is needed
    finally:
        await tenv.close()


@pytest.mark.parametrize(
    ("blank", "problem"),
    [
        (k.PUBLISHED_VERIFIED_VALUE, f"{k.PUBLISHED_VERIFIED_VALUE} is not configured"),
        (k.PUBLISHED_VERIFIED_FIELD, f"{k.PUBLISHED_VERIFIED_FIELD} is not configured"),
        (k.notice_key("approved", "description"), f"{k.notice_key('approved', 'description')} is not configured"),
        (k.notice_key("rejected", "description"), f"{k.notice_key('rejected', 'description')} is not configured"),
    ],
)
async def test_half_configured_pairs_are_reported(tmp_path: Path, blank: str, problem: str) -> None:
    tenv = await make_test_env(tmp_path, content={blank: ""})
    try:
        status = tenv.app.readiness.statuses[k.REVIEWS]
        assert not status.ready
        assert any(p.kind == "invalid_content" and p.message.startswith(problem) for p in status.problems)
    finally:
        await tenv.close()


async def test_notice_without_title_is_fine(tmp_path: Path) -> None:
    titles = {k.notice_key(decision, "title"): "" for decision in k.NOTICE_DECISIONS}
    tenv = await make_test_env(tmp_path, content=titles)
    try:
        assert tenv.app.ready(k.REVIEWS)
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _order(conn: sqlite3.Connection, number: int) -> int:
    cursor = conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, status, currency, subtotal_minor, total_minor, "
        "payment_provider, created_at, updated_at) VALUES (1, ?, 5, 'fulfilled', 'USD', 1, 1, 'manual', 0, 0)",
        (number,),
    )
    return int(cursor.lastrowid or 0)


def test_migration_keeps_existing_reviews() -> None:
    migrations = discover()
    names = [m.name for m in migrations]
    assert "reviews_approval" in names
    position = names.index("reviews_approval")
    conn = _connect()
    apply_migrations(conn, migrations=migrations[:position])
    rows = [
        ("published", 4, "x", 99),
        ("rated", 3, "", None),  # submitted, waiting for publication
        ("rated", 2, None, None),  # comment form not submitted
        ("prompted", None, None, None),
        ("declined", None, None, None),
    ]
    for number, (status, rating, comment, message_id) in enumerate(rows, start=1):
        order_id = _order(conn, number)
        conn.execute(
            "INSERT INTO reviews(guild_id, order_id, user_id, status, rating, comment, published_message_id, "
            "created_at, updated_at) VALUES (1, ?, 5, ?, ?, ?, ?, 0, 0)",
            (order_id, status, rating, comment, message_id),
        )
    apply_migrations(conn, migrations=migrations)
    migrated = {
        r["id"]: (r["status"], r["rating"], r["comment"], r["published_message_id"], r["approval"])
        for r in conn.execute("SELECT * FROM reviews ORDER BY id")
    }
    assert migrated == {
        1: ("published", 4, "x", 99, "automatic"),
        2: ("rated", 3, "", None, "automatic"),
        3: ("rated", 2, None, None, None),
        4: ("prompted", None, None, None, None),
        5: ("declined", None, None, None, None),
    }
    assert repo.submitted_review_ids(conn, 1) == [2]
    # new rows keep increasing ids
    order_id = _order(conn, 99)
    cursor = conn.execute(
        "INSERT INTO reviews(guild_id, order_id, user_id, status, created_at, updated_at) "
        "VALUES (1, ?, 5, 'prompted', 0, 0)",
        (order_id,),
    )
    assert cursor.lastrowid == 6


@pytest.mark.parametrize(
    "update",
    [
        "UPDATE reviews SET status = 'pending_approval' WHERE id = 1",  # without rating and comment
        "UPDATE reviews SET status = 'rated', rating = 3, comment = 'a' WHERE id = 1",  # submitted, not approved
        "UPDATE reviews SET status = 'published', rating = 3, comment = 'a' WHERE id = 1",
        "UPDATE reviews SET status = 'rejected', rating = 3, comment = 'a' WHERE id = 1",
        "UPDATE reviews SET approval = 'approved' WHERE id = 1",  # a staff decision without who and when
        "UPDATE reviews SET decision_reason = 'r' WHERE id = 1",
        "UPDATE reviews SET approval_message_id = 5 WHERE id = 1",  # without its channel
        "UPDATE reviews SET status = 'pending_approval', rating = 3, comment = 'a', approval = 'automatic' "
        "WHERE id = 1",
    ],
)
def test_schema_rejects_inconsistent_review_states(update: str) -> None:
    conn = _connect()
    apply_migrations(conn)
    order_id = _order(conn, 1)
    conn.execute(
        "INSERT INTO reviews(guild_id, order_id, user_id, status, created_at, updated_at) "
        "VALUES (1, ?, 5, 'prompted', 0, 0)",
        (order_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(update)


def test_approval_message_is_unique() -> None:
    conn = _connect()
    apply_migrations(conn)
    for number in (1, 2):
        order_id = _order(conn, number)
        conn.execute(
            "INSERT INTO reviews(guild_id, order_id, user_id, status, rating, comment, approval_channel_id, "
            "approval_message_id, created_at, updated_at) VALUES (1, ?, 5, 'pending_approval', 3, '', 7, ?, 0, 0)",
            (order_id, 100 if number == 1 else 101),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE reviews SET approval_message_id = 100 WHERE id = 2")


def test_repository_decisions_apply_once() -> None:
    conn = _connect()
    apply_migrations(conn)
    order_id = _order(conn, 1)
    conn.execute(
        "INSERT INTO reviews(guild_id, order_id, user_id, status, rating, comment, created_at, updated_at) "
        "VALUES (1, ?, 5, 'pending_approval', 3, 'c', 0, 0)",
        (order_id,),
    )
    first = repo.approve(conn, 1, actor_id=9, now=10, task_attempts=5)
    assert first.outcome is repo.Outcome.OK and first.review is not None and first.review.submitted
    second = repo.reject(conn, 1, actor_id=9, reason=None, now=11, task_attempts=5)
    assert second.outcome is repo.Outcome.NOT_PENDING
    assert repo.approve(conn, 1, actor_id=9, now=12, task_attempts=5).outcome is repo.Outcome.NOT_PENDING
    assert repo.approve(conn, 99, actor_id=9, now=12, task_attempts=5).outcome is repo.Outcome.NOT_FOUND
    with pytest.raises(ValueError):
        repo.reject(conn, 1, actor_id=9, reason="x" * (repo.REASON_MAX + 1), now=13, task_attempts=5)
    tasks = conn.execute("SELECT kind, dedupe_key, status FROM scheduled_tasks").fetchall()
    assert [tuple(t) for t in tasks] == [(repo.DECISION_TASK, repo.decision_key(1), "pending")]


# --------------------------------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------------------------------


async def test_approval_preview_states(ap: TestEnv) -> None:
    assert MODULE.preview is not None
    states = {s.name: s for s in MODULE.preview(ap.app)}
    for name in (
        "approval_request",
        "approval_request_longest_comment",
        "approval_request_without_comment",
        "approval_approved",
        "approval_rejected",
        "approval_removed",
        "thanks_pending_approval",
        "notice_approved",
        "notice_rejected",
        "staff_reject_form",
        "staff_decision_reply",
    ):
        state = states[name]
        spec = state.spec
        problems = validate_modal(spec) if isinstance(spec, ModalSpec) else validate_message(spec)  # type: ignore[arg-type]
        assert problems == [], (name, problems)
        assert unrouted_components(state, ap.app.router) == [], name
        assert state.note
    number = number_text(ap, 1)
    pending = states["approval_request"].spec
    assert isinstance(pending, MessageSpec)
    buttons = [c for row in pending.rows for c in row]
    assert all(isinstance(b, ButtonSpec) for b in buttons)
    assert [b.custom_id for b in buttons] == [views.approve_id(1), views.reject_id(1)]  # type: ignore[union-attr]
    assert [b.style for b in buttons] == ["success", "danger"]  # type: ignore[union-attr]
    embed = pending.embeds[0]
    assert embed.title == rendered(ap, k.APPROVAL_TITLE, rating=5, user_name="[sample member]", order_number=number)
    assert (embed.fields[2].name, embed.fields[2].value) == (
        ap.text(k.APPROVAL_CUSTOMER_FIELD),
        f"<@{SAMPLE_MEMBER_ID}>",
    )
    for decided in ("approval_approved", "approval_rejected", "approval_removed"):
        spec = states[decided].spec
        assert isinstance(spec, MessageSpec) and spec.rows == ()
    rejected = states["approval_rejected"].spec
    assert isinstance(rejected, MessageSpec)
    assert rejected.embeds[0].fields[-1].name == ap.text(k.APPROVAL_REASON_FIELD)
    assert rejected.embeds[0].fields[-1].value.startswith(SAMPLE_REASON)
    assert len(rejected.embeds[0].fields[-1].value) == repo.REASON_MAX
    notice = states["notice_rejected"].spec
    assert isinstance(notice, MessageSpec)
    assert notice.embeds[0].description == rendered(ap, k.notice_key("rejected", "description"), order_number=number)


async def test_approval_states_fit_with_the_largest_settings(tmp_path: Path) -> None:
    settings = make_settings(approval_overrides({"reviews": {"rating_scale": 10, "comment_max_length": 4000}}))
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        assert MODULE.preview is not None
        for state in MODULE.preview(tenv.app):
            spec = state.spec
            problems = validate_modal(spec) if isinstance(spec, ModalSpec) else validate_message(spec)  # type: ignore[arg-type]
            assert problems == [], (state.name, problems)
    finally:
        await tenv.close()


async def test_optional_states_are_skipped_when_not_configured(tmp_path: Path) -> None:
    off = {k.notice_key(decision, "description"): "" for decision in k.NOTICE_DECISIONS}
    off.update({k.notice_key(decision, "title"): "" for decision in k.NOTICE_DECISIONS})
    tenv = await make_test_env(tmp_path, content=off)
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        assert "notice_approved" not in names and "notice_rejected" not in names
        assert "approval_request" in names  # built even while approval is off, for the limits check
    finally:
        await tenv.close()
