#!/usr/bin/env bash
# Automated verification entry point for Learning Workbench.
#   scripts/verify.sh            deterministic tests (no model, no network, no Desktop)
#   scripts/verify.sh --host     + Hermes host checks in the isolated test home (plugins doctor/validate)
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"
runtime="${LW_HERMES_RUNTIME:-$HOME/.hermes/hermes-agent}"
py="$(command -v python3)"
deps="$repo/.test-artifacts/pydeps"
if [[ ! -d "$deps/pytest" && ! -d "$deps/_pytest" ]]; then
  uv pip install --quiet --target "$deps" "pytest>=8,<10" --python "$py"
fi
echo "== JS syntax check (desktop/plugin.js)"
node --check desktop/plugin.js
echo "== deterministic tests"
PYTHONPATH="$deps:$runtime:$repo/tests" "$py" -m pytest -q -c tests/pytest.ini --rootdir tests tests "$@"
