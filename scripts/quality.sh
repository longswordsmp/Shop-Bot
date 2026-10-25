#!/usr/bin/env bash
# Run every quality gate. Exits non-zero on the first failing gate.
# Usage: scripts/quality.sh            (uses .venv/bin if present, else the current Python environment)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then BIN=.venv/bin/; else BIN=""; fi

step() { printf '\n==> %s\n' "$*"; }

step "Syntax/import check";   "${BIN}python" -m compileall -q shopbot tools tests
step "Format check";          "${BIN}ruff" format --check shopbot tools tests
step "Lint";                  "${BIN}ruff" check shopbot tools tests
step "Type check";            "${BIN}mypy"
step "Tests";                 "${BIN}pytest" -q -p no:cacheprovider
step "Discord limits + handler coverage (configured text)"; "${BIN}python" -m tools.check_limits --all-features
step "Discord limits + handler coverage (every state, synthetic text)"
"${BIN}python" -m tools.check_limits --all-features --fixtures --strict
step "Secret scan";           "${BIN}python" -m tools.secret_scan
step "Generated docs are current"
GENERATED="docs/COMMANDS.md docs/COMPONENTS.md docs/DATABASE.md docs/CONTENT_CHECKLIST.md"
before=$(cat $GENERATED 2>/dev/null | sha256sum)
"${BIN}python" -m tools.reference_docs >/dev/null
after=$(cat $GENERATED | sha256sum)
if [ "$before" != "$after" ]; then
  echo "Generated docs were out of date and have been regenerated; review and commit them." >&2
  exit 1
fi
printf '\nAll quality gates passed.\n'
