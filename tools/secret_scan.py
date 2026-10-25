"""Scan tracked files for committed secrets.

Usage:
    python -m tools.secret_scan          # scans files tracked by git (and staged files)

Fails (exit 1) on: Discord bot tokens, common API key formats, private keys, populated ``.env`` files, and
SQLite databases. Test fixtures that intentionally use fake token *shapes* must not match real formats.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS: dict[str, re.Pattern[str]] = {
    "discord_bot_token": re.compile(r"\b[MNO][A-Za-z\d_-]{23,27}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,40}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "stripe_secret": re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    "slack_token": re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}\b"),
}
FORBIDDEN_NAMES = re.compile(r"(^|/)\.env(\.(?!example$)[^/]+)?$|\.(db|sqlite3?|db-wal|db-shm)$")


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if line]


def scan() -> list[str]:
    findings: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if FORBIDDEN_NAMES.search(relative):
            findings.append(f"{relative}: file type must never be committed")
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{relative}:{line}: possible {name}")
    return findings


def main() -> int:
    findings = scan()
    if findings:
        print("Possible secrets found:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print("No secrets found in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
