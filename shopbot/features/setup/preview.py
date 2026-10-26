"""Preview of the /setup report and the /reset messages (operator output) with obviously synthetic sample data.

Covers a typical report, a dry-run plan, and a report long enough to be split across several messages,
so the offline limits checker exercises the packer. Every sample includes the entry-channel warning (unmanaged
channels everyone can see). The /reset samples include a server with hundreds of channels (the name lists are cut
to fit) and a result with failures.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from shopbot.config.banner_ids import BannerId
from shopbot.features.server_keys import SERVER
from shopbot.features.setup import reset
from shopbot.features.setup import reset_views as rv
from shopbot.features.setup.report import SetupReport, report_messages
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState

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
    # Entry-channel safety: unmanaged channels everyone can see (e.g. Discord's default #general and General voice).
    for index, kind in enumerate(("text", "voice", "category"), start=4):
        report.add(
            "problems",
            app.op(
                "server.setup.public_unmanaged",
                kind=app.op(f"server.setup.channel_kind.{kind}"),
                target=f"<#10000000000000000{index}>",
                name=f"[sample unmanaged {kind}]",
            ),
        )
    if size > 25:
        report.add("problems", app.op("server.setup.public_unmanaged_more", count=size - 25))
    report.add("problems", app.op("server.setup.public_unmanaged_hint"))
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
    return states + reset_states(app)


_KEPT = (
    reset.Kept(reset.ROLE, "[sample bot role]", reset.KEEP_MANAGED),
    reset.Kept(reset.ROLE, "[sample server booster]", reset.KEEP_MANAGED),
    reset.Kept(reset.ROLE, "[sample role above the bot]", reset.KEEP_ABOVE_BOT),
    reset.Kept(reset.CHANNEL, "[sample-rules]", reset.KEEP_COMMUNITY),
    reset.Kept(reset.CHANNEL, "[sample-hidden]", reset.KEEP_NO_PERMISSION),
)


def _plan(size: int, *, wipe: bool) -> rv.PlanView:
    return rv.PlanView(
        server_name="[sample server]",
        channels=tuple(f"[sample-channel-{i}]" for i in range(1, size + 1)),
        categories=tuple(f"[sample category {i}]" for i in range(1, max(2, size // 6) + 1)),
        roles=tuple(f"[sample role {i}]" for i in range(1, max(3, size // 4) + 1)),
        kept=_KEPT,
        open_orders=3,
        open_tickets=2,
        wipe=wipe,
        fingerprint="0123456789abcdef",
    )


def _result(*, failures: int, wipe: bool) -> reset.ResetResult:
    result = reset.ResetResult(
        backup="before-reset-20240101T000000Z.db",
        wipe=wipe,
        deleted=Counter({reset.CHANNEL: 24, reset.CATEGORY: 6, reset.ROLE: 7}),
        kept=len(_KEPT),
        setup_channel_id=100000000000000009,
    )
    if wipe:
        result.wiped = {"orders": 120, "products": 40, "reviews": 35}
    result.failures = [
        reset.Failure(reset.CHANNEL, f"[sample-channel-{i}]", 403, 50013) for i in range(1, failures + 1)
    ]
    return result


def reset_states(app: App) -> list[PreviewState]:
    ui, op = app.ui, app.op
    note = "Owner-only /reset"
    cases: list[tuple[str, str, MessageSpec | ModalSpec]] = [
        (
            "reset_preview",
            f"{note}: what would be deleted and kept (sample data).",
            rv.preview_message(ui, op, _plan(24, wipe=False)),
        ),
        (
            "reset_preview_wipe",
            f"{note} with wipe_shop_data (sample data).",
            rv.preview_message(ui, op, _plan(24, wipe=True)),
        ),
        (
            "reset_preview_large",
            f"{note} on a server with 400 channels: lists cut to fit.",
            rv.preview_message(ui, op, _plan(400, wipe=False)),
        ),
        (
            "reset_form",
            f"{note}: the confirmation form.",
            rv.confirm_modal(op, "[sample server]", wipe=False, fingerprint="0123456789abcdef"),
        ),
        (
            "reset_form_wipe",
            f"{note}: the confirmation form with the wipe.",
            rv.confirm_modal(op, "[sample server]", wipe=True, fingerprint="0123456789abcdef"),
        ),
        ("reset_progress", f"{note}: the progress DM.", rv.progress_message(ui, op, 20, 37, 0)),
        ("reset_result", f"{note}: the result DM.", rv.result_message(ui, op, _result(failures=0, wipe=False))),
        (
            "reset_result_wipe",
            f"{note}: the result DM after a wipe.",
            rv.result_message(ui, op, _result(failures=0, wipe=True)),
        ),
        (
            "reset_result_problems",
            f"{note}: the result DM with failures.",
            rv.result_message(ui, op, _result(failures=60, wipe=False)),
        ),
    ]
    replies: tuple[tuple[str, str, BannerId], ...] = (
        ("server.reset.owner_only", "danger", "error"),
        ("server.reset.cancelled", "info", "cancellation"),
        ("server.reset.changed", "warning", "cancellation"),
        ("server.reset.dm_closed", "danger", "error"),
        ("server.reset.started", "warning", "submission-and-processing"),
    )
    for key, tone, banner in replies:
        name = f"reset_{key.rsplit('.', 1)[1]}"
        cases.append((name, f"{note}: a short reply.", rv.notice(ui, op(key), tone=tone, banner=banner)))
    return [PreviewState(feature=SERVER, name=name, note=text, spec=spec) for name, text, spec in cases]
