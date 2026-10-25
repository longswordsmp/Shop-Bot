from __future__ import annotations

import dataclasses
import re

from shopbot.config.readiness import FeatureStatus
from shopbot.features.base import FeatureModule
from shopbot.features.setup import MODULE
from shopbot.features.setup.report import SECTIONS, SetupReport, report_messages
from shopbot.ui import limits
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import MessageSpec
from tests.helpers import TestEnv
from tests.setup.util import setup

_LEFTOVER = re.compile(r"\{[a-z_]+\}")


def _field_values(messages: list[MessageSpec], name: str) -> list[str]:
    return [f.value for m in messages for e in m.embeds for f in e.fields if f.name == name]


def test_operator_text_has_no_unknown_placeholders(tenv: TestEnv) -> None:
    allowed = {
        "{created}",
        "{adopted}",
        "{updated}",
        "{unchanged}",
        "{skipped}",
        "{failed}",
        "{user_id}",
        "{permissions}",
        "{name}",
        "{item}",
        "{target}",
        "{changes}",
        "{kind}",
        "{kind_lower}",
        "{content_key}",
        "{id}",
        "{category}",
        "{action}",
        "{http_status}",
        "{code}",
        "{feature}",
        "{error_id}",
        "{count}",
    }
    for key, text in MODULE.operator_text.items():
        assert key.startswith("server.")
        assert set(_LEFTOVER.findall(text)) <= allowed, key


async def test_report_lines_render_without_leftover_placeholders(tenv: TestEnv) -> None:
    for dry_run in (True, False):
        report = await setup(tenv, dry_run=dry_run)
        for message in report_messages(tenv.app.ui, tenv.app.op, report):
            assert validate_message(message) == []
            for embed in message.embeds:
                for text in [embed.title or "", embed.description or ""] + [f.value for f in embed.fields]:
                    assert not _LEFTOVER.search(text), text


def test_large_report_is_split_without_losing_any_line(tenv: TestEnv) -> None:
    app = tenv.app
    report = SetupReport()
    expected: dict[str, list[str]] = {}
    for section in SECTIONS:
        lines = [f"`{section}_{i}`: sample line {i} " + "x" * (i % 90) for i in range(400)]
        expected[section] = lines
        for line in lines:
            report.add(section, line, "unchanged")
    messages = report_messages(app.ui, app.op, report)
    assert len(messages) > 1
    for message in messages:
        assert validate_message(message) == []
        assert len(message.embeds) <= limits.EMBEDS_PER_MESSAGE
        assert sum(e.text_length() for e in message.embeds) <= limits.EMBED_TOTAL_CHARS
    assert messages[0].embeds[0].title == app.op("server.setup.title")
    assert all(m.embeds[0].title == app.op("server.setup.title_continued") for m in messages[1:])
    for section in SECTIONS:
        values = _field_values(messages, app.op(f"server.setup.section.{section}"))
        assert "\n".join(values).split("\n") == expected[section]


def test_many_small_fields_respect_field_and_embed_counts(tenv: TestEnv) -> None:
    app = tenv.app
    from shopbot.features.setup.report import pack_fields
    from shopbot.ui.spec import EmbedField

    fields = [EmbedField(f"n{i}", "v") for i in range(400)]
    messages = pack_fields(app.ui, title="t", description=None, continued_title="c", fields=fields)
    assert sum(len(e.fields) for m in messages for e in m.embeds) == 400
    for message in messages:
        assert validate_message(message) == []
        assert all(len(e.fields) <= limits.EMBED_FIELDS for e in message.embeds)


def test_overlong_line_is_split_not_truncated(tenv: TestEnv) -> None:
    app = tenv.app
    report = SetupReport()
    line = "y" * 2500
    report.add("problems", line)
    messages = report_messages(app.ui, app.op, report)
    values = _field_values(messages, app.op("server.setup.section.problems"))
    assert "".join(v.replace("\n", "") for v in values) == line
    for message in messages:
        assert validate_message(message) == []


async def test_publish_hooks_run_for_ready_features_and_failures_are_isolated(tenv: TestEnv) -> None:
    app = tenv.app
    calls: list[str] = []

    async def ok_publish(app_: object) -> list[str]:
        calls.append("ok")
        return ["[sample panel line]"]

    async def broken_publish(app_: object) -> list[str]:
        calls.append("broken")
        raise RuntimeError("secret internal detail")

    async def not_ready_publish(app_: object) -> list[str]:
        calls.append("not_ready")
        return []

    extra = [
        FeatureModule(name="zz_broken", is_enabled=lambda s: True, publish=broken_publish),
        FeatureModule(name="zz_ok", is_enabled=lambda s: True, publish=ok_publish),
        FeatureModule(name="zz_not_ready", is_enabled=lambda s: True, publish=not_ready_publish),
    ]
    app.modules = [*app.modules, *extra]
    app.readiness.statuses["zz_broken"] = FeatureStatus("zz_broken", True, True)
    app.readiness.statuses["zz_ok"] = FeatureStatus("zz_ok", True, True)
    app.readiness.statuses["zz_not_ready"] = FeatureStatus("zz_not_ready", True, False, problems=[])

    report = await setup(tenv)
    assert calls == ["broken", "ok"]  # module order; not-ready features are never published
    panels = "\n".join(report.lines["panels"])
    assert "`zz_broken`" in panels and "secret" not in panels
    assert "[sample panel line]" in report.lines["panels"]
    assert any("`zz_not_ready`" in line for line in report.lines["problems"])
    assert app.op("server.setup.status_hint") in report.lines["problems"]
    assert len([line for line in report.lines["panels"] if "`zz_broken`" in line]) == 1


async def test_publish_disabled_skips_hooks(tenv: TestEnv) -> None:
    app = tenv.app
    calls: list[str] = []

    async def publish(app_: object) -> list[str]:
        calls.append("x")
        return []

    assert app.module("info_panels") is not None
    app.modules = [dataclasses.replace(m, publish=publish) if m.name == "info_panels" else m for m in app.modules]
    report = await setup(tenv, publish=False)
    assert calls == []
    assert report.lines["panels"] == [app.op("server.setup.publish_skipped")]
    await setup(tenv)
    assert calls == ["x"]


def test_preview_states_pass_limits(tenv: TestEnv) -> None:
    assert MODULE.preview is not None
    states = MODULE.preview(tenv.app)
    names = [s.name for s in states]
    assert "report" in names and "report_dry_run" in names
    assert any(name.startswith("report_large_part_") for name in names)
    for state in states:
        assert state.feature == "server"
        assert isinstance(state.spec, MessageSpec)
        assert validate_message(state.spec) == [], state.name
