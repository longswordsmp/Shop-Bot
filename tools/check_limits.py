"""Offline checker: builds every configured message, form, and panel state without connecting to Discord
and validates them against Discord's limits, plus every configured content value against its key.

Usage:
    python -m tools.check_limits                 # check the current configuration (enabled features)
    python -m tools.check_limits --all-features  # also build states of disabled features
    python -m tools.check_limits --config-dir path/to/config

Exit status: 0 when no violation was found, 1 when any limit violation or invalid value exists, 2 when the
configuration cannot be loaded. States that cannot be built because text is not configured yet are listed
as "incomplete" (they are not sent to customers either); use --strict to treat them as failures.
Configured text is never truncated: every violation is reported with the exact path and length.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

from shopbot.config.content import ContentFileError, MissingContent
from shopbot.config.settings import SettingsError
from shopbot.ui import custom_ids
from shopbot.ui.limits import Violation, validate_message, validate_modal
from shopbot.ui.routing import Router
from shopbot.ui.spec import ButtonSpec, MessageSpec, ModalSpec, PreviewState, SelectSpec
from tools.offline import ROOT, TrackingContentStore, build_offline_app


@dataclass
class Report:
    states_checked: int = 0
    violations: list[tuple[str, Violation]] = field(default_factory=list)
    value_problems: list[tuple[str, str]] = field(default_factory=list)
    incomplete: dict[str, set[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    unrouted: list[tuple[str, str]] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.violations or self.value_problems or self.errors or self.unrouted)


def check_state(state: PreviewState) -> list[Violation]:
    if isinstance(state.spec, ModalSpec):
        return validate_modal(state.spec)
    if isinstance(state.spec, MessageSpec):
        return validate_message(state.spec)
    return [Violation(state.name, f"unknown spec type {type(state.spec).__name__}")]


def unrouted_components(state: PreviewState, router: Router) -> list[str]:
    """custom_ids in a state that no registered handler would receive (a button that would do nothing)."""
    known = {(r.kind, r.route, r.action) for r in router.routes()}
    missing: list[str] = []
    if isinstance(state.spec, ModalSpec):
        targets = [("modal", state.spec.custom_id)]
    else:
        targets = []
        for row in state.spec.rows:
            for component in row:
                if isinstance(component, ButtonSpec) and component.style == "link":
                    continue
                if isinstance(component, (ButtonSpec, SelectSpec)) and component.custom_id:
                    targets.append(("component", component.custom_id))
    for kind, custom_id in targets:
        parsed = custom_ids.parse(custom_id)
        if parsed is None or (kind, parsed[0], parsed[1]) not in known:
            missing.append(custom_id)
    return missing


async def run_checks(config_dir: Path, *, all_features: bool) -> Report:
    report = Report()
    app = await build_offline_app(config_dir, content_mode="actual", all_features=all_features)
    try:
        store = app.content
        assert isinstance(store, TrackingContentStore)
        for problem in store.value_problems():
            report.value_problems.append((problem.key, problem.message))
        for module in app.modules:
            if module.preview is None:
                continue
            if not all_features and not app.readiness.is_enabled(module.name):
                continue
            store.missing.clear()
            try:
                states = module.preview(app)
            except MissingContent as exc:
                report.incomplete.setdefault(module.name, set()).add(exc.key)
                continue
            except Exception as exc:  # a preview must never crash the checker
                report.errors.append(f"{module.name}: preview failed: {type(exc).__name__}: {exc}")
                continue
            if store.missing:
                report.incomplete.setdefault(module.name, set()).update(store.missing)
            for state in states:
                report.states_checked += 1
                for violation in check_state(state):
                    report.violations.append((f"{state.feature}/{state.name}", violation))
                for custom_id in unrouted_components(state, app.router):
                    report.unrouted.append((f"{state.feature}/{state.name}", custom_id))
    finally:
        await app.db.close()
    return report


def print_report(report: Report, *, strict: bool) -> int:
    print(f"States built and checked: {report.states_checked}")
    if report.value_problems:
        print(f"\nInvalid content values ({len(report.value_problems)}):")
        for key, message in report.value_problems:
            print(f"  - {key}: {message}")
    if report.violations:
        print(f"\nDiscord limit violations ({len(report.violations)}):")
        for state, violation in report.violations:
            print(f"  - {state}: {violation}")
    if report.unrouted:
        print(f"\nComponents without a registered handler ({len(report.unrouted)}):")
        for state, custom_id in report.unrouted:
            print(f"  - {state}: {custom_id}")
    if report.incomplete:
        total = sum(len(v) for v in report.incomplete.values())
        print(f"\nIncomplete (text not configured yet; these states are not shown to customers): {total} keys")
        for feature, keys in sorted(report.incomplete.items()):
            print(f"  {feature}: {len(keys)} missing keys (run `python -m tools.checklist` for the full list)")
    if report.errors:
        print("\nErrors:")
        for error in report.errors:
            print(f"  - {error}")
    failed = report.failed or (strict and bool(report.incomplete))
    print("\nRESULT:", "FAIL" if failed else "OK")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--all-features", action="store_true", help="also check features that are disabled")
    parser.add_argument("--strict", action="store_true", help="fail when any state is incomplete")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_checks(args.config_dir, all_features=args.all_features))
    except (SettingsError, ContentFileError) as exc:
        print("Configuration cannot be loaded:")
        for error in exc.errors:
            print(f"  - {error}")
        return 2
    return print_report(report, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
