"""Admin view builders stay within Discord limits for any data, and only use defined operator text."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shopbot.config.content_keys import Kind, ck
from shopbot.config.readiness import FeatureStatus, Problem
from shopbot.db.repos.audit import AuditEvent
from shopbot.db.repos.tasks import Task
from shopbot.features.admin import MODULE, service, views
from shopbot.features.admin.text import OPERATOR_TEXT
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec
from tests.helpers import TestEnv

PACKAGE = Path(__file__).resolve().parents[2] / "shopbot" / "features" / "admin"


def long_key(i: int, length: int = 90) -> str:
    base = f"feature.group.key_{i:04d}_"
    return base + "x" * (length - len(base))


def all_valid(specs: list[object]) -> None:
    for spec in specs:
        assert validate_message(spec) == [], spec  # type: ignore[arg-type]


async def test_preview_shows_only_the_status_overview_with_its_version(tenv: TestEnv) -> None:
    assert MODULE.preview is not None
    (state,) = MODULE.preview(tenv.app)
    assert state.feature == "admin" and state.name == "status" and state.note
    assert isinstance(state.spec, MessageSpec) and validate_message(state.spec) == []
    assert tenv.app.op("admin.status.version", version="0123abc") in (state.spec.embeds[0].description or "")
    assert MODULE.customer_facing is False
    assert MODULE.content_keys(tenv.app.settings) == []
    assert MODULE.is_enabled(tenv.app.settings)


def test_every_operator_text_key_used_in_code_is_defined() -> None:
    used: set[str] = set()
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"""op\(\s*["'](admin\.[a-z0-9_.]+)["']""", source))
        used |= set(re.findall(r"""["'](admin\.[a-z0-9_.]+)["']\s+if\b""", source))
        used |= set(re.findall(r"""\belse\s+["'](admin\.[a-z0-9_.]+)["']""", source))
    for kind in ("missing_content", "invalid_content", "settings", "dependency"):
        used.add(f"admin.problem.kind.{kind}")
    missing = sorted(k for k in used if k not in OPERATOR_TEXT)
    assert missing == []
    assert len(used) > 60
    assert all(key.startswith("admin.") for key in OPERATOR_TEXT)


def test_operator_text_never_uses_reserved_key_placeholder() -> None:
    # app.op(key, **values): a {key} placeholder would collide with the parameter name.
    assert not any("{key}" in text for text in OPERATOR_TEXT.values())


async def test_status_overview_with_extreme_data(tenv: TestEnv) -> None:
    snapshot = service.StatusSnapshot(
        features=tuple(service.FeatureLine(f"feature_{i}", i % 2 == 0, i % 3 == 0, i * 7) for i in range(60)),
        unknown_keys=tuple(long_key(i) for i in range(500)),
        guild_available=True,
        missing_permissions=tuple((f"cap{i}", ("manage_roles", "manage_channels", "attach_files")) for i in range(80)),
        message_content_intent=False,
        intents_not_enabled=("message_content", "members"),
        members_intent=False,
        transcripts_need_intent=True,
        task_counts={"pending": 10**9, "failed": 3},
        db_path="/data/" + "d" * 3000 + "/bot.db",
    )
    spec = views.status_overview(tenv.app.ui, tenv.app.op, snapshot)
    all_valid([spec])
    text = "\n".join(f.value for f in spec.embeds[0].fields)
    assert "… and " in text


@pytest.mark.parametrize("count", [0, 1, 40, 2000])
async def test_status_feature_pages_with_many_long_problems(tenv: TestEnv, count: int) -> None:
    problems = [
        Problem("x", "invalid_content", "is too long; " + "m" * 1500, long_key(i))
        if i % 5 == 0
        # unkeyed, very long messages (e.g. settings problems) are clipped at the field limit
        else Problem("x", "settings", "s" * 3000)
        if i % 7 == 0
        else Problem("x", "missing_content", "not configured", long_key(i))
        for i in range(count)
    ]
    status = FeatureStatus("x", enabled=True, ready=not problems, problems=problems)
    first = views.status_feature(tenv.app.ui, tenv.app.op, "x", status, 1)
    footer = first.embeds[0].footer_text
    pages = int(footer.rsplit(" ", 1)[-1]) if footer else 1
    sample = sorted({0, 1, 2, pages // 2, pages - 1, pages, pages + 1})  # rendering is per page (one click)
    specs = [views.status_feature(tenv.app.ui, tenv.app.op, "x", status, p) for p in sample]
    all_valid(specs)
    if pages > 1:
        assert specs[0] == specs[1]  # page 0 clamps to the first page
        assert specs[-1].embeds[0].footer_text == f"Page {pages} of {pages}"


async def test_content_views_with_extreme_values(tenv: TestEnv) -> None:
    ui, op = tenv.app.ui, tenv.app.op
    for value in ("v" * 4000, "v" * 4089, "```", "`edge`", "line\n" * 800, "x"):
        saved = views.content_saved(ui, op, long_key(1), value, [])
        all_valid([saved.spec])
        if saved.attachment is None:
            assert saved.spec.embeds[0].description == f"```\n{value}\n```"
        else:
            assert saved.attachment == ("value.txt", value)
        invalid = views.content_invalid(ui, op, long_key(1), value, ["p" * 900] * 30)
        all_valid([invalid.spec])
    changes = [
        service.ReadinessChange(
            f"feature_{i}", service.FeatureLine("a", True, False, 3), service.FeatureLine("a", True, True, 0)
        )
        for i in range(400)
    ]
    all_valid([views.content_saved(ui, op, "k.k", "v", changes).spec])
    cleared = service.ClearResult(key=long_key(2), removed=True, source_after=None, changes=tuple(changes))
    all_valid([views.content_cleared(ui, op, cleared)])


async def test_content_show_with_maximum_values(tenv: TestEnv) -> None:
    definition = ck(
        long_key(3), Kind.DESCRIPTION, "f" * 50, "g" * 300, "doc " * 600, placeholders=tuple(f"p{i}" for i in range(80))
    )
    info = service.KeyInfo(
        definition=definition,
        raw="r" * 4096,
        source="file",
        fallback_used=None,
        required=True,
        problems=tuple("problem " * 50 for _ in range(40)),
    )
    reply = views.content_show(tenv.app.ui, tenv.app.op, info)
    all_valid([reply.spec])
    assert reply.attachment == ("value.txt", "r" * 4096)


async def test_content_modal_limits(tenv: TestEnv) -> None:
    op = tenv.app.op
    for key, current in ((long_key(4, 95), None), ("a.b", "c" * 4000), ("a.b", "c" * 4096), ("a.b", "")):
        spec = views.content_modal(op, "tok_EN-12345", key, current)
        assert validate_modal(spec) == []
        expected_default = current if current is not None and len(current) <= 4000 else None
        assert spec.inputs[0].default == expected_default


async def test_content_lists_with_many_entries(tenv: TestEnv) -> None:
    ui, op = tenv.app.ui, tenv.app.op
    grouped_problems = [
        (f"feature_{f}", [Problem("x", "missing_content", "not configured", long_key(i)) for i in range(120)])
        for f in range(8)
    ]
    rows = [
        (
            f"feature_{f}",
            [service.KeyRow(long_key(i), "description", i % 2 == 0, None, False, i % 9 == 0) for i in range(150)],
        )
        for f in range(8)
    ]
    for page in (1, 2, 50, 10_000):
        all_valid([views.content_missing(ui, op, None, grouped_problems, page)])
        all_valid([views.content_keys(ui, op, None, rows, page)])
        all_valid([views.content_keys(ui, op, "feature_1", rows[:1], page)])
    assert views.content_missing(ui, op, None, grouped_problems, 1).rows


async def test_report_lists_split_into_valid_messages(tenv: TestEnv) -> None:
    ui, op = tenv.app.ui, tenv.app.op
    events = [
        AuditEvent(i, 1, None if i % 2 else 10**18, "a" * 100, "t" * 50, "i" * 50, {"k": "v" * 5000}, 1_700_000_000)
        for i in range(100)
    ]
    audit = views.audit_list(ui, op, events)
    all_valid(audit)
    assert len(audit) > 1
    tasks = [Task(i, "kind_" + "k" * 80, 1_700_000_000, {}, "failed", 5, 5, None, "error " * 400) for i in range(50)]
    all_valid(views.tasks_list(ui, op, tasks, {"failed": 50}))
    report = service.ReloadReport(
        ok=False,
        errors=tuple(f"settings.toml: problem {i} " + "e" * 300 for i in range(300)),
        features=tuple(service.FeatureLine(f"f{i}", True, False, i) for i in range(10)),
    )
    reload_specs = views.reload_report(ui, op, report)
    all_valid(reload_specs)
    assert len(reload_specs) > 1
    all_valid(views.reload_report(ui, op, service.ReloadReport(ok=True, errors=(), features=report.features)))
    all_valid([views.backup_report(ui, op, path="/" + "p" * 3000, size=10**12, kept=10, removed=5)])
    all_valid([views.unknown_feature(ui, op, "n" * 500, [f"feature_{i}" for i in range(500)])])
    all_valid([views.unknown_key(ui, op, "k" * 300, [long_key(i) for i in range(5)])])


def test_bounded_list_respects_limit() -> None:
    def op(key: str, **values: object) -> str:
        return OPERATOR_TEXT[key].format(**values) if values else OPERATOR_TEXT[key]

    items = [f"item-{i:04d}-" + "z" * 40 for i in range(1000)]
    text = views.bounded_list(op, items, 1000)
    assert len(text) <= 1000
    assert text.endswith("more")
    assert views.bounded_list(op, ["short"], 1000) == "short"
    assert views.bounded_list(op, [], 1000) == "none"
    assert len(views.bounded_list(op, ["q" * 5000], 1000)) <= 1000


def test_code_block_only_when_exact() -> None:
    assert views.code_block("plain") == "```\nplain\n```"
    assert views.code_block("has ``` fence") is None
    assert views.code_block("`starts") is None
    assert views.code_block("ends`") is None
    assert views.code_block("x" * 4089) is None
    assert views.code_block("x" * 4088) is not None
