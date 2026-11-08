#!/usr/bin/env bash
# S4 host lifecycle in a FRESH isolated Hermes home, against a clean export of a commit:
#   validate (catalog gate) -> install from git at a pinned SHA -> doctor -> create learner data ->
#   disable/enable -> reinstall over (update path) -> uninstall (data kept) -> reinstall (data back).
# Output: .test-artifacts/lifecycle.txt. Usage: scripts/lifecycle-check.sh [commit]   (default HEAD)
set -uo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sha="$(git -C "$repo" rev-parse "${1:-HEAD}")"
export LW_HERMES_HOME="${LW_TEST_ROOT:-$HOME/.hermes/cache/scratch/hackathon-learning-workbench}/lifecycle-home"
# shellcheck source=testenv.sh
source "$repo/scripts/testenv.sh"
out="$repo/.test-artifacts/lifecycle.txt"
export_dir="$LW_TEST_ROOT/clean-export"
"$LW_PYTHON" -c 'import shutil, sys; [shutil.rmtree(p, ignore_errors=True) for p in sys.argv[1:]]' "$HERMES_HOME" "$export_dir"
mkdir -p "$HERMES_HOME" "$export_dir"
git -C "$repo" archive "$sha" | tar -x -C "$export_dir"
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
print(json.dumps({"study": bool(view), "attempts": [a["answer"] for e in (view or {}).get("exercises", []) for a in e["attempts"]],
                  "db": str(db.data_dir() / "data.db")}))
PY
}

{
  echo "# lifecycle check $(date -Is)"
  echo "commit: $sha"
  echo "HERMES_HOME: $HERMES_HOME (suffix $HERMES_DATA_DIR_SUFFIX, lazy installs disabled)"
  echo; echo "## isolation"; "$repo/scripts/lw-isolation-check" | tail -1
  echo; echo "## validate (clean export of $sha)"
  H plugins validate "$export_dir"; v=$?
  check "validate passes on a clean export" test $v -eq 0
  echo; echo "## install from git at the pinned SHA"
  H plugins install "file://$repo" --ref "$sha" --enable --yes-deps; check "install exits 0" test $? -eq 0
  H plugins list | grep -E "learning-workbench" ; check "listed as enabled" bash -c "\"$LW_PYTHON\" -m hermes_cli.main plugins list 2>&1 | grep learning-workbench | grep -q enabled"
  echo; echo "## doctor (installed copy)"
  H plugins doctor "$HERMES_HOME/plugins/learning-workbench"; check "doctor passes" test $? -eq 0
  echo; echo "## learner data"
  p=$(probe create); echo "$p"; check "study and verbatim attempt stored" grep -q '"attempts": \["four"\]' <<<"$p"
  echo; echo "## disable / enable"
  H plugins disable learning-workbench; check "disable exits 0" test $? -eq 0
  H plugins list | grep learning-workbench
  p=$(probe read); check "data intact while disabled" grep -q '"attempts": \["four"\]' <<<"$p"
  H plugins enable learning-workbench; check "enable exits 0" test $? -eq 0
  echo; echo "## reinstall over the installed copy (update path)"
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
