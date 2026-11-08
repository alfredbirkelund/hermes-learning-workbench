#!/usr/bin/env python3
"""Mutation check: break one invariant at a time in a scratch copy and require the named tests to FAIL.

Proves the deterministic tests can detect the defects they claim to guard against.
Usage: scripts/mutation-check.py  (run from the repo root; uses scripts/verify.sh's pytest setup)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = os.environ.get("LW_HERMES_RUNTIME", str(Path.home() / ".hermes" / "hermes-agent"))
DEPS = REPO / ".test-artifacts" / "pydeps"

MUTATIONS = [
    ("first check posed without a lesson", "lw/core.py",
     "                if checked_before is None:\n                    raise LwError(\"lesson_required\"",
     "                if False:\n                    raise LwError(\"lesson_required\"",
     "tests/test_lessons.py::test_a_steps_first_check_needs_a_lesson_and_a_refusal_stores_nothing"),
    ("schema 1 store left un-migrated", "lw/db.py",
     "    2: [\"ALTER TABLE exercises ADD COLUMN lesson TEXT NOT NULL DEFAULT ''\"],",
     "    2: [],",
     "tests/test_lessons.py::test_a_v1_store_is_upgraded_in_place_and_keeps_every_record"),
    ("newer store opened anyway", "lw/db.py",
     "    if found is not None and found > SCHEMA_VERSION:\n        raise _too_new(found)",
     "    if False:\n        raise _too_new(found)",
     "tests/test_lessons.py::test_a_store_from_a_newer_version_is_refused_untouched_with_a_clear_message"),
    ("chat help ignored in help class", "lw/core.py",
     "    elif chat_seen:", "    elif False:",
     "tests/test_chat_help.py::test_chat_while_an_exercise_is_open_steers_the_agent_and_labels_the_next_answer"),
    ("pane messages counted as chat help", "lw/tools.py",
     "        from_pane = isinstance(user_message, str) and", "        from_pane = False and",
     "tests/test_chat_help.py::test_the_panes_own_messages_are_not_chat_help_but_still_carry_the_reminder"),
    ("compaction creates a store for non-users", "__init__.py",
     "        if not db.store_exists():  # no store", "        if False:  # no store",
     "tests/test_review_fixes.py::test_compaction_in_a_profile_that_never_studied_creates_nothing"),
    ("finished studies unreachable from /study", "lw/commands.py",
     "        if verb in (\"export\", \"delete\"):\n            picked,", "        if False:\n            picked,",
     "tests/test_review_fixes.py::test_a_finished_study_stays_reachable_from_the_text_door"),
    ("export names can collide", "lw/export.py",
     "            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666))\n",
     "            pass\n",
     "tests/test_review_fixes.py::test_two_exports_in_the_same_second_get_their_own_files"),
    ("feedback ignored in help class", "lw/core.py",
     "    elif feedback_seen:", "    elif False:",
     "tests/test_review_fixes.py::test_a_retry_after_a_check_is_labelled_after_feedback"),
    ("busy store escapes /study", "lw/commands.py",
     "    except sqlite3.OperationalError:\n        logger.warning(\"learning-workbench: /study found",
     "    except ZeroDivisionError:\n        logger.warning(\"learning-workbench: /study found",
     "tests/test_review_fixes.py::test_a_busy_store_gets_the_plugins_message_in_the_text_door"),
    ("quote verification disabled", "lw/core.py",
     "if not any(needle in h for h in haystacks if h):", "if False:",
     "tests/test_learning_loop.py::test_verdict_must_quote_the_learner_and_nothing_is_recorded_otherwise"),
    ("hints ignored in help class", "lw/core.py",
     "    elif hints_seen:\n        cls = \"hinted\"", "    elif False:\n        cls = \"hinted\"",
     "tests/test_learning_loop.py::test_hinted_and_revealed_answers_never_become_unaided_after_reload"),
    ("submit dedupe removed", "lw/core.py",
     "        if dup is not None:\n            if dup[\"exercise_id\"] != exercise_id or dup[\"answer\"]",
     "        if False:\n            if dup[\"exercise_id\"] != exercise_id or dup[\"answer\"]",
     "tests/test_learner_door.py::test_repeated_submit_with_one_request_id_creates_one_attempt"),
    ("unknown agent fields accepted", "lw/tools.py",
     "    if unknown:\n        raise core.LwError(\"unknown_fields\"", "    if False:\n        raise core.LwError(\"unknown_fields\"",
     "tests/test_agent_tools.py::test_forged_learner_fields_are_rejected_and_create_nothing"),
    ("second open exercise allowed", "lw/core.py",
     "        if open_ex is not None:\n            waiting", "        if False:\n            waiting",
     "tests/test_agent_tools.py::test_only_one_exercise_can_be_open"),
    ("draft revision check removed", "lw/core.py",
     "        if base != current:\n            raise LwError(\"draft_conflict\"",
     "        if False:\n            raise LwError(\"draft_conflict\"",
     "tests/test_learner_door.py::test_two_windows_editing_one_draft_conflict_instead_of_overwriting"),
    ("chat binding not enforced", "lw/core.py",
     "    if study[\"hermes_session_id\"] != session_id:", "    if False:",
     "tests/test_agent_tools.py::test_another_chat_cannot_write_into_this_study"),
    ("stale learner revision ignored", "lw/core.py",
     "    if seen < _learner_revision(conn, study[\"id\"]):", "    if False:",
     "tests/test_learning_loop.py::test_learner_route_edit_makes_stale_agent_writes_fail"),
    ("contest-less re-assessment allowed", "lw/core.py",
     "            if latest is not None:\n                raise LwError(\"already_assessed\"",
     "            if False:\n                raise LwError(\"already_assessed\"",
     "tests/test_learning_loop.py::test_contest_keeps_the_earlier_verdict"),
    ("delete token not checked", "lw/core.py",
     "        if row is None or not secrets.compare_digest(", "        if False and not secrets.compare_digest(",
     "tests/test_learner_door.py::test_delete_needs_two_steps_and_touches_only_the_selected_study"),
    ("compaction rebind removed", "__init__.py",
     "        study_id = core.rebind_session(", "        study_id = None and core.rebind_session(",
     "tests/test_lifecycle.py::test_compaction_that_rotates_the_session_keeps_the_study_attached"),
    ("solution shown before reveal", "lw/core.py",
     "            if revealed:\n                item[\"reference_answer\"]", "            if True:\n                item[\"reference_answer\"]",
     "tests/test_learning_loop.py::test_solution_stays_hidden_until_an_explicit_reveal"),
]


def run_test(root: Path, test: str) -> int:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(DEPS), RUNTIME, str(root / "tests")])}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-c", "tests/pytest.ini", "--rootdir", "tests",
                           test], cwd=root, env=env, capture_output=True, text=True, timeout=300)
    return proc.returncode


def main() -> int:
    survivors = []
    with tempfile.TemporaryDirectory(prefix="lw-mutation-") as tmp:
        for label, rel, old, new, test in MUTATIONS:
            root = Path(tmp) / "repo"
            if root.exists():
                shutil.rmtree(root)
            shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", ".test-artifacts", "__pycache__"))
            target = root / rel
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"SETUP-ERROR  {label}: pattern found {text.count(old)} times in {rel}")
                survivors.append(label)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            code = run_test(root, test)
            status = "killed  " if code != 0 else "SURVIVED"
            print(f"{status} {label}  ->  {test.split('::')[1]}")
            if code == 0:
                survivors.append(label)
        clean = Path(tmp) / "repo"
        if clean.exists():
            shutil.rmtree(clean)
        shutil.copytree(REPO, clean, ignore=shutil.ignore_patterns(".git", ".test-artifacts", "__pycache__"))
        baseline = [run_test(clean, t) for *_, t in MUTATIONS]
        print("unmutated copy: all named tests pass" if not any(baseline) else f"unmutated copy FAILED: {baseline}")
        if any(baseline):
            return 2
    print(f"{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} mutations killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
