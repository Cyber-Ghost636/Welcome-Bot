#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if [[ ! -d .venv ]]; then
  echo "ERROR: Missing .venv in $REPO_DIR"
  exit 1
fi

source .venv/bin/activate

if command -v rg >/dev/null 2>&1; then
  if rg -n "@app\.commands\." --glob "*.py" . >/dev/null; then
    echo "ERROR: Found invalid decorator namespace (@app.commands.*). Use @app_commands.* instead."
    rg -n "@app\.commands\." --glob "*.py" .
    exit 1
  fi
else
  if grep -RIn --include "*.py" "@app\.commands\." . >/dev/null; then
    echo "ERROR: Found invalid decorator namespace (@app.commands.*). Use @app_commands.* instead."
    grep -RIn --include "*.py" "@app\.commands\." .
    exit 1
  fi
fi

python -m py_compile *.py

echo "Pre-restart checks passed."
