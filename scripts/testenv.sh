# Source this file (bash/zsh) or use scripts/lw-env (wrapper) to run a command inside the
# isolated Learning Workbench test environment. Nothing here touches the user's real Hermes home.
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

: "${LW_TEST_ROOT:=$HOME/.hermes/cache/scratch/hackathon-learning-workbench}"
export LW_TEST_ROOT
export HERMES_HOME="${LW_HERMES_HOME:-$LW_TEST_ROOT/hermes-home}"
export HERMES_DATA_DIR_SUFFIX="-lw-isolated"
export HERMES_GATEWAY_LOCK_DIR="$LW_TEST_ROOT/gateway-locks"
export LW_DESKTOP_USER_DATA="$LW_TEST_ROOT/desktop-user-data"
mkdir -p "$HERMES_HOME" "$HERMES_GATEWAY_LOCK_DIR" "$LW_DESKTOP_USER_DATA"

case "$HERMES_HOME" in
  "$HOME/.hermes"|"$HOME/.hermes/") echo "refusing: HERMES_HOME points at the real home" >&2; return 1 2>/dev/null || exit 1;;
esac
