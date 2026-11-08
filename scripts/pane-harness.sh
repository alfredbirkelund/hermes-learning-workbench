#!/usr/bin/env bash
# Pane logic harness: real desktop/plugin.js + real plugin backend, host stand-ins, jsdom. Not a visual check.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${LW_HERMES_RUNTIME:-$HOME/.hermes/hermes-agent}"
work="$repo/.test-artifacts/pane-harness"
mkdir -p "$work"
if [[ ! -d "$work/node_modules/react-dom" ]]; then
  (cd "$work" && printf '{ "name": "lw-pane-harness", "private": true, "type": "module" }\n' > package.json &&
   npm install --silent --no-audit --no-fund react@19.2.7 react-dom@19.2.7 nanostores@1.4.2 @nanostores/react@1.1.0 jsdom@26)
fi
cp "$repo/tests/e2e/pane/sdk-stub.mjs" "$repo/tests/e2e/pane/pane-harness.mjs" "$work/"
sed "s#from '@hermes/plugin-sdk'#from './sdk-stub.mjs'#" "$repo/desktop/plugin.js" > "$work/plugin.mjs"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/lw-pane-home.XXXXXX")"
log="$work/server.log"
for v in $(compgen -e | grep '^HERMES_' || true); do unset "$v"; done  # hermetic server environment
PYTHONPATH="$runtime" HOME="$scratch/os-home" python3 "$repo/tests/e2e/pane_server.py" "$scratch/hermes-home" > "$log" 2>&1 &
server=$!
cleanup() {
  kill "$server" 2>/dev/null || true
  python3 -c 'import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$scratch"
}
trap cleanup EXIT
for _ in $(seq 1 100); do grep -q '^PORT ' "$log" && break; sleep 0.1; done
port="$(sed -n 's/^PORT //p' "$log")"
[[ -n "$port" ]] || { echo "server did not start"; cat "$log"; exit 1; }
for _ in $(seq 1 100); do curl -sf "http://127.0.0.1:$port/api/plugins/learning-workbench/health" >/dev/null && break; sleep 0.1; done
cd "$work" && LW_PANE_PORT="$port" node pane-harness.mjs
