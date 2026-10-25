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
step "Discord limits + handler coverage"; "${BIN}python" -m tools.check_limits --all-features
step "Secret scan";           "${BIN}python" -m tools.secret_scan
step "Generated docs are current"
"${BIN}python" -m tools.reference_docs >/dev/null
if ! git diff --quiet -- docs/; then
  echo "Generated docs are out of date; run 'python -m tools.reference_docs' and commit the result." >&2
  git --no-pager diff --stat -- docs/ >&2
  exit 1
fi
printf '\nAll quality gates passed.\n'
