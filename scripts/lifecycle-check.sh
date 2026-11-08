#!/usr/bin/env bash
# S4 host lifecycle in a FRESH isolated Hermes home, against a clean export of a commit:
#   validate (catalog gate) -> install the previous release from a git source that tracks main -> write learner
#   data with it -> move main to the candidate and run `hermes plugins update` (data migrated, not lost) -> doctor ->
#   disable/enable -> forced reinstall at a pinned SHA -> uninstall (data kept) -> reinstall (data back).
# Output: .test-artifacts/lifecycle.txt.
# Usage: scripts/lifecycle-check.sh [candidate commit, default HEAD]; LW_BASE_REF = previous release (default 0c563e0,
# the last schema-1 commit).
set -uo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sha="$(git -C "$repo" rev-parse "${1:-HEAD}")"
base="$(git -C "$repo" rev-parse "${LW_BASE_REF:-0c563e0}")"
export LW_HERMES_HOME="${LW_TEST_ROOT:-$HOME/.hermes/cache/scratch/hackathon-learning-workbench}/lifecycle-home"
# shellcheck source=testenv.sh
source "$repo/scripts/testenv.sh"
out="$repo/.test-artifacts/lifecycle.txt"
export_dir="$LW_TEST_ROOT/clean-export"
# Fresh home for every run, except Hermes's downloaded runtime caches (tools/, installs/, cache/): rebuilding them
# is a multi-minute network download that tests nothing about the plugin.
"$LW_PYTHON" - "$HERMES_HOME" "$export_dir" <<'PY'
import shutil, sys
from pathlib import Path
home, export = Path(sys.argv[1]), Path(sys.argv[2])
if home.is_dir():
    for child in home.iterdir():
        if child.name in {"tools", "installs", "cache"}:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
shutil.rmtree(export, ignore_errors=True)
PY
mkdir -p "$HERMES_HOME" "$export_dir"
git -C "$repo" archive "$sha" | tar -x -C "$export_dir"
# The git source the install tracks: its main starts at the previous release and later moves to the candidate.
src="$LW_TEST_ROOT/lifecycle-src"
"$LW_PYTHON" -c 'import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$src"
git clone -q "$repo" "$src" && git -C "$src" checkout -q -B main "$base"
H() { "$LW_PYTHON" -m hermes_cli.main "$@" 2>&1 | sed -E 's/\x1b\[[0-9;]*m//g'; return "${PIPESTATUS[0]}"; }
fails=0
check() { local label="$1"; shift; if "$@"; then echo "PASS $label"; else echo "FAIL $label"; fails=$((fails+1)); fi; }

probe() {  # run the INSTALLED plugin's own code in a fresh process; prints one JSON line
  "$LW_PYTHON" - "$HERMES_HOME/plugins/learning-workbench" "$1" <<'PY'
import importlib.util, json, sys, types
sys.modules["hermes_bootstrap"] = None
stub = types.ModuleType("tui_gateway.server"); stub._broadcast_global_event = lambda *a: None
sys.modules["tui_gateway.server"] = stub
plugin_dir, action = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("lw_probe_api", plugin_dir + "/dashboard/plugin_api.py")
api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
core, db = api.core, api.db
conn = db.connect()
if action == "create":
    s = core.start_study(conn, actor="agent", session_id="life-chat", goal="Lifecycle goal")
    ex = core.propose_exercise(conn, session_id="life-chat", study_id=s["study_id"], study_revision=1, kind="diagnostic",
                               prompt="2 + 2?", reference_answer="4", rubric="4")
    core.submit_attempt(conn, study_id=s["study_id"], exercise_id=ex["exercise_id"], client_request_id="life-req-0001",
                        answer="four", reasoning="counting", channel="desktop")
row = core.active_study_for_session(conn, "life-chat")
view = core.study_view(conn, row["id"], audience="learner") if row else None
schema = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
print(json.dumps({"study": bool(view), "attempts": [a["answer"] for e in (view or {}).get("exercises", []) for a in e["attempts"]],
                  "schema": schema, "db": str(db.data_dir() / "data.db")}))
PY
}

{
  echo "# lifecycle check $(date -Is)"
  echo "commit: $sha (previous release: $base)"
  echo "HERMES_HOME: $HERMES_HOME (suffix $HERMES_DATA_DIR_SUFFIX, lazy installs disabled)"
  echo; echo "## isolation"; "$repo/scripts/lw-isolation-check" | tail -1
  echo; echo "## validate (clean export of $sha)"
  H plugins validate "$export_dir"; v=$?
  check "validate passes on a clean export" test $v -eq 0
  echo; echo "## install the previous release ($base) from a git source that tracks main"
  H plugins install "file://$src" --enable --yes-deps; check "install exits 0" test $? -eq 0
  # Rich truncates names to 15 characters in a non-TTY table ("learning-workb…"), so check the JSON listing.
  H plugins list --json 2>/dev/null | "$LW_PYTHON" -c 'import json,sys; [print(p["name"], p["status"], p.get("version")) for p in json.load(sys.stdin) if p["name"] == "learning-workbench"]'
  check "listed as enabled" bash -c "\"$LW_PYTHON\" -m hermes_cli.main plugins list --json 2>/dev/null | \"$LW_PYTHON\" -c 'import json,sys; sys.exit(0 if any(p[\"name\"] == \"learning-workbench\" and p[\"status\"] == \"enabled\" for p in json.load(sys.stdin)) else 1)'"
  echo; echo "## learner data written by the previous release"
  p=$(probe create); echo "$p"; check "study and verbatim attempt stored" grep -q '"attempts": \["four"\]' <<<"$p"
  check "previous release wrote schema 1" grep -q '"schema": "1"' <<<"$p"
  echo; echo "## update to the candidate with \`hermes plugins update\`"
  git -C "$src" reset -q --hard "$sha"
  H plugins update learning-workbench --yes < /dev/null; check "update exits 0" test $? -eq 0
  installed="$HERMES_HOME/plugins/learning-workbench"
  check "installed copy is the candidate" bash -c "cmp -s '$export_dir/lw/db.py' '$installed/lw/db.py' && cmp -s '$export_dir/plugin.yaml' '$installed/plugin.yaml'"
  p=$(probe read); echo "$p"; check "data intact after update" grep -q '"attempts": \["four"\]' <<<"$p"
  check "store migrated to schema 2" grep -q '"schema": "2"' <<<"$p"
  echo; echo "## validate --install-deps (clean export, after the install prepared this home's runtime)"
  H plugins validate "$export_dir" --install-deps; check "validate --install-deps passes" test $? -eq 0
  echo; echo "## doctor (installed copy)"
  H plugins doctor "$HERMES_HOME/plugins/learning-workbench"; check "doctor passes" test $? -eq 0
  echo; echo "## disable / enable"
  H plugins disable learning-workbench; check "disable exits 0" test $? -eq 0
  H plugins list --json 2>/dev/null | "$LW_PYTHON" -c 'import json,sys; [print(p["name"], p["status"]) for p in json.load(sys.stdin) if p["name"] == "learning-workbench"]'
  p=$(probe read); check "data intact while disabled" grep -q '"attempts": \["four"\]' <<<"$p"
  H plugins enable learning-workbench; check "enable exits 0" test $? -eq 0
  echo; echo "## forced reinstall over the installed copy, pinned to the candidate SHA"
  H plugins install "file://$repo" --ref "$sha" --force --enable --yes-deps; check "forced reinstall exits 0" test $? -eq 0
  p=$(probe read); check "data intact after reinstall" grep -q '"attempts": \["four"\]' <<<"$p"
  echo; echo "## uninstall keeps learner data"
  H plugins remove learning-workbench; check "remove exits 0" test $? -eq 0
  check "plugin directory removed" test ! -e "$HERMES_HOME/plugins/learning-workbench"
  check "data file kept after uninstall" test -s "$HERMES_HOME/plugin-data/learning-workbench/data.db"
  echo; echo "## reinstall restores access to the same records"
  H plugins install "file://$repo" --ref "$sha" --enable --yes-deps; check "reinstall exits 0" test $? -eq 0
  p=$(probe read); echo "$p"; check "same study and attempt after reinstall" grep -q '"attempts": \["four"\]' <<<"$p"
  echo; echo "RESULT: $fails failure(s)"
} 2>&1 | tee "$out"
exit $([[ $(tail -1 "$out") == "RESULT: 0 failure(s)" ]] && echo 0 || echo 1)
