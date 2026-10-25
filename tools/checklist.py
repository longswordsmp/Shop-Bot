"""Generate the content checklist: every customer-facing text field, grouped by feature, with status.

Usage:
    python -m tools.checklist                       # print to stdout (all features)
    python -m tools.checklist --write               # write docs/CONTENT_CHECKLIST.md
    python -m tools.checklist --enabled-only        # only features enabled in settings.toml
    python -m tools.checklist --template            # (re)write config/content.toml with every key, keeping values

Keys depend on settings (e.g. one set of keys per ticket category). The checklist reflects the current
settings.toml. With the default (all features), disabled features are listed too, using preview
assumptions only for business decisions that are still unset (never written back to settings).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from shopbot.config.content import ContentFileError
from shopbot.config.content_keys import ContentKey
from shopbot.config.settings import SettingsError
from shopbot.context import App
from tools.offline import ROOT, build_offline_app

CHECKLIST_PATH = ROOT / "docs" / "CONTENT_CHECKLIST.md"
CONTENT_PATH = ROOT / "config" / "content.toml"

FEATURE_TITLES = {
    "core": "Shared (errors, buttons, formats, branding)",
    "server": "Server structure (role, category, channel names)",
    "info_panels": "Information panels",
    "tickets": "Tickets",
    "catalog": "Catalog",
    "checkout": "Checkout, orders and payment",
    "credits": "Store credit",
    "reviews": "Reviews",
}


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _requirement(definition: ContentKey, shared: set[str]) -> str:
    if definition.required:
        return "required"
    if definition.key in shared:
        return "required (used by an enabled feature)"
    if definition.fallback:
        return f"optional (falls back to `{definition.fallback}`)"
    return "optional"


def build_checklist(app: App, *, enabled_only: bool) -> str:
    content = app.content
    shared: set[str] = set()
    for module in app.modules:
        if app.readiness.is_enabled(module.name) or not enabled_only:
            shared.update(module.shared_keys(app.settings))
    lines: list[str] = [
        "# Content checklist",
        "",
        "Every piece of text customers can see, by feature. Fill values in `config/content.toml`",
        "(or with `/content set` in Discord). Empty means not configured: the related panel or message",
        "is not shown until its required keys are set. Configured text is used exactly as written.",
        "Placeholders such as `{number}` are replaced with data; other text, including braces, is kept as-is.",
        "",
        "Regenerate with `python -m tools.checklist --write` after changing settings.toml.",
        "",
    ]
    statuses = app.readiness.statuses
    total_required = total_missing = 0
    for module in app.modules:
        keys = content.registry.for_feature(module.name)
        if not keys or (enabled_only and not statuses[module.name].enabled):
            continue
        status = statuses.get(module.name)
        state = "enabled" if status and status.enabled else "disabled in settings"
        lines += [f"## {FEATURE_TITLES.get(module.name, module.name)}", "", f"Feature `{module.name}` — {state}.", ""]
        groups: dict[str, list[ContentKey]] = {}
        for definition in keys:
            groups.setdefault(definition.group, []).append(definition)
        for group, definitions in groups.items():
            lines += [
                f"### {group}",
                "",
                "| | Key | Where it appears | Kind (max chars) | Required | Placeholders |",
                "|---|---|---|---|---|---|",
            ]
            for definition in definitions:
                configured = content.raw(definition.key) is not None
                required = definition.required or definition.key in shared
                if required:
                    total_required += 1
                    total_missing += 0 if configured else 1
                mark = "✅" if configured else ("❌" if required else "▫️")
                placeholders = ", ".join(f"`{{{p}}}`" for p in definition.placeholders) or ""
                lines.append(
                    f"| {mark} | `{definition.key}` | {_md(definition.doc)} | {definition.kind.value} "
                    f"({definition.max_length}) | {_requirement(definition, shared)} | {placeholders} |"
                )
            lines.append("")
    decisions = [p for s in statuses.values() for p in s.problems if p.kind == "settings"]
    lines += ["## Business decisions and settings still open", ""]
    if decisions:
        lines += [f"- **{p.feature}**: {p.message}" for p in decisions]
    else:
        lines.append("- None reported for the listed features.")
    lines += [
        "",
        "## Summary",
        "",
        f"- Required keys (for the listed features): {total_required}",
        f"- Required keys still missing: {total_missing}",
        "",
        "Legend: ✅ configured · ❌ required and missing · ▫️ optional and empty",
        "",
    ]
    return "\n".join(lines)


async def _run(config_dir: Path, *, enabled_only: bool, write: bool, template: bool) -> int:
    app = await build_offline_app(config_dir, content_mode="actual", all_features=not enabled_only)
    try:
        text = build_checklist(app, enabled_only=enabled_only)
        if write:
            CHECKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            CHECKLIST_PATH.write_text(text, encoding="utf-8")
            print(f"Wrote {CHECKLIST_PATH}")
        if template:
            from shopbot.features.admin.content_toml import render_content_toml

            values = {d.key: app.content.raw(d.key) or "" for d in app.content.registry.all()}
            CONTENT_PATH.write_text(render_content_toml(app.content.registry, values), encoding="utf-8")
            print(f"Wrote {CONTENT_PATH} ({len(values)} keys; existing values preserved)")
        if not write and not template:
            print(text)
    finally:
        await app.db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--enabled-only", action="store_true")
    parser.add_argument("--write", action="store_true", help="write docs/CONTENT_CHECKLIST.md")
    parser.add_argument("--template", action="store_true", help="rewrite config/content.toml with all keys")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(
            _run(args.config_dir, enabled_only=args.enabled_only, write=args.write, template=args.template)
        )
    except (SettingsError, ContentFileError) as exc:
        print("Configuration cannot be loaded:")
        for error in exc.errors:
            print(f"  - {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
