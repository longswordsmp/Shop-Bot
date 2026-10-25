"""Preview of the /setup report (operator output) with obviously synthetic sample data.

Covers a typical report, a dry-run plan, and a report long enough to be split across several messages,
so the offline limits checker exercises the packer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.server_keys import SERVER
from shopbot.features.setup.report import SetupReport, report_messages
from shopbot.ui.spec import PreviewState

if TYPE_CHECKING:
    from shopbot.context import App


def _sample_report(app: App, *, dry_run: bool, size: int) -> SetupReport:
    report = SetupReport(dry_run=dry_run, publish=not dry_run)
    sections = (
        ("roles", "role", "<@&100000000000000001>"),
        ("categories", "category", "<#100000000000000002>"),
        ("channels", "channel", "<#100000000000000003>"),
    )
    for section, kind, target in sections:
        for index in range(1, size + 1):
            key = f"sample_{kind}_{index}"
            if dry_run:
                name = app.op("server.setup.target_name", name=f"[sample {kind} {index}]")
                report.add(section, app.op("server.setup.would_create", item=key, target=name), "created")
            elif index % 3 == 0:
                changes = ", ".join(app.op(f"server.setup.change.{c}") for c in ("name", "overwrites"))
                report.add(section, app.op("server.setup.updated", item=key, target=target, changes=changes), "updated")
            else:
                report.add(section, app.op("server.setup.unchanged", item=key, target=target), "unchanged")
    if dry_run:
        report.add("panels", app.op("server.setup.publish_skipped_dry_run"))
    else:
        report.add("panels", app.op("server.setup.publish_failed", feature="sample_feature", error_id="0123456789ab"))
    report.add("problems", app.op("server.setup.missing_permissions", permissions="manage_roles"))
    report.add("problems", app.op("server.setup.feature_not_ready", feature="sample_feature", count=3))
    report.add("problems", app.op("server.setup.status_hint"))
    return report


def preview(app: App) -> list[PreviewState]:
    states: list[PreviewState] = []
    cases = (
        ("report", "Admin-only /setup report (sample data).", False, 4),
        ("report_dry_run", "Admin-only /setup dry-run plan (sample data).", True, 4),
        ("report_large", "A long /setup report split across several messages (sample data).", False, 120),
    )
    for name, note, dry_run, size in cases:
        messages = report_messages(app.ui, app.op, _sample_report(app, dry_run=dry_run, size=size))
        for index, spec in enumerate(messages, start=1):
            suffix = f"_part_{index}" if len(messages) > 1 else ""
            states.append(PreviewState(feature=SERVER, name=f"{name}{suffix}", note=note, spec=spec))
    return states
