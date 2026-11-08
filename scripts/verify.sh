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
stamp="$repo/.test-artifacts/hermes-deps.stamp"
want="$(git -C "$runtime" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ ! -d "$deps/pytest" && ! -d "$deps/_pytest" ]] || [[ "$(cat "$stamp" 2>/dev/null || true)" != "$want" ]]; then
  echo "== installing the Hermes checkout's locked dependencies into .test-artifacts/pydeps"
  mkdir -p "$deps" "$repo/.test-artifacts"
  uv export --project "$runtime" --frozen --no-dev --no-hashes --no-emit-project --no-header --no-annotate \
    > "$repo/.test-artifacts/hermes-core-reqs.txt"
  uv pip install --quiet --no-deps --target "$deps" -r "$repo/.test-artifacts/hermes-core-reqs.txt" --python "$py"
  uv pip install --quiet --target "$deps" "pytest>=8,<10" --python "$py"
  echo "$want" > "$stamp"
fi
echo "== JS syntax check (desktop/plugin.js)"
node --check desktop/plugin.js
echo "== deterministic tests"
PYTHONPATH="$deps:$runtime:$repo/tests" "$py" -m pytest -q -c tests/pytest.ini --rootdir tests tests "$@"
