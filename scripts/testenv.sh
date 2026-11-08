# Source this file (bash/zsh) or use scripts/lw-env (wrapper) to run a command inside the
# isolated Learning Workbench test environment. Nothing here writes to the user's real Hermes home.
#
#   LW_TEST_ROOT   root for all isolated state (default: Hermes scratch dir below)
#   HERMES_HOME    isolated Hermes home (never ~/.hermes)
#   HERMES_GATEWAY_LOCK_DIR  isolates the per-OS-user "one gateway per host" rendezvous, so test
#                  processes never attach to (or restart) the user's host gateway.
#   HERMES_DATA_DIR_SUFFIX  moves Hermes's "native default home" away from ~/.hermes. Required because
#                  the assigned test home lives under ~/.hermes/cache/scratch, and Hermes treats any
#                  HERMES_HOME under ~/.hermes as part of the real root (default profile = ~/.hermes,
#                  machine spawn ledger = ~/.hermes/spawn-ledger.json). With the suffix the test home
#                  is its own custom root. scripts/lw-isolation-check proves it.
#   HERMES_DISABLE_LAZY_INSTALLS=1  a custom root has no dependency environment of its own; this stops
#                  Hermes from building one (or repairing launchers) and runs the installed interpreter
#                  read-only instead.
#   LW_PYTHON      that installed interpreter, resolved BEFORE entering the isolated environment.
#                  Run Hermes as "$LW_PYTHON" -m hermes_cli.main ... (scripts/lw-env hermes ... does it).

: "${LW_TEST_ROOT:=$HOME/.hermes/cache/scratch/hackathon-learning-workbench}"
export LW_TEST_ROOT
# Hermetic: drop every HERMES_* variable inherited from the calling session (a parent agent exports its
# session id, profile pin, real home and more), then set only the test ones below.
for _lw_var in $(compgen -e | grep '^HERMES_' || true); do unset "$_lw_var"; done
unset _lw_var
if [[ -z "${LW_PYTHON:-}" ]]; then
  _lw_runtime="${LW_HERMES_RUNTIME:-$HOME/.hermes/hermes-agent}"
  _lw_rc="$(env -u HERMES_HOME -u HERMES_DATA_DIR_SUFFIX "$_lw_runtime/.hermes/bin/hermes" --print-runtime-command)"
  _lw_store="$(/usr/bin/python3 -c 'import json,sys; print(json.loads(sys.argv[1])[0])' "$_lw_rc")"
  LW_PYTHON="$(env -u HERMES_HOME -u HERMES_DATA_DIR_SUFFIX "$_lw_store" -I -c '
import sys; from pathlib import Path
r = Path(sys.argv[1]); sys.path.insert(0, str(r))
from pm.environments import committed_venv
e = committed_venv(r); print(e / "bin/python" if e else "")' "$_lw_runtime")"
  unset _lw_runtime _lw_rc _lw_store
fi
export LW_PYTHON
export HERMES_HOME="${LW_HERMES_HOME:-$LW_TEST_ROOT/hermes-home}"
export HERMES_DATA_DIR_SUFFIX="-lw-isolated"
export HERMES_DISABLE_LAZY_INSTALLS=1
export HERMES_GATEWAY_LOCK_DIR="$LW_TEST_ROOT/gateway-locks/$(basename "$HERMES_HOME")"
export LW_DESKTOP_USER_DATA="${LW_DESKTOP_USER_DATA:-$LW_TEST_ROOT/desktop-user-data}"
mkdir -p "$HERMES_HOME" "$HERMES_GATEWAY_LOCK_DIR" "$LW_DESKTOP_USER_DATA"

case "$HERMES_HOME" in
  "$HOME/.hermes"|"$HOME/.hermes/") echo "refusing: HERMES_HOME points at the real home" >&2; return 1 2>/dev/null || exit 1;;
esac
if [[ ! -x "$LW_PYTHON" ]]; then
  echo "refusing: no installed Hermes interpreter found (LW_PYTHON)" >&2; return 1 2>/dev/null || exit 1
fi
