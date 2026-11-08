"""Fixes from the independent review: finished studies stay reachable, no store for non-users, honest file errors."""
import json
import os
import re
from pathlib import Path

import pytest

from test_lifecycle import _command

REPO = Path(__file__).resolve().parents[1]


def _finish_with_one_attempt(env):
    study = env.start()
    sid = study["study_id"]
    eid = env.pose(study)["exercise_id"]
    code, _ = env.submit(sid, eid, "x² + 2xh + h²")
    assert code == 200
    return sid


def test_a_finished_study_stays_reachable_from_the_text_door(env):
    run = _command(env)
    sid = _finish_with_one_attempt(env)
    done = run("finish confirm")
    assert "number 1" in done and "/study export 1" in done and "/study delete 1" in done
    listing = run("")
    assert "Finished in this chat:" in listing and "1. Understand why d/dx x² = 2x" in listing
    exported = run("export 1")
    assert exported.startswith("Learning record written to ")
    path = Path(exported.removeprefix("Learning record written to ").strip())
    assert "x² + 2xh + h²" in path.read_text(encoding="utf-8")
    assert run("export 2") == "There is no finished study 2 in this chat. /study lists them."
    code, body = env.api("GET", "/session-study?session_id=chat-A")
    assert code == 200 and body["study"] is None and [s["study_id"] for s in body["earlier"]] == [sid]
    code, req = env.api("POST", f"/studies/{sid}/delete-request", {})
    assert code == 200
    gone = run(f"delete 1 confirm {req['confirm_token']}")
    assert gone.startswith("Deleted the study and its records (1 attempt(s)).")
    assert not path.exists()
    assert run("export 1") == "This chat has no finished studies."
    assert env.rows("SELECT COUNT(*) AS n FROM studies")[0]["n"] == 0


def test_another_chats_finished_studies_are_not_reachable_by_number(env):
    run = _command(env)
    _finish_with_one_attempt(env)
    assert run("finish confirm", ).startswith("Study finished.")
    assert _command(env)("export 1", session_id="chat-B") == "This chat has no finished studies."
    assert "Finished in this chat" not in run("", session_id="chat-B")


def test_compaction_in_a_profile_that_never_studied_creates_nothing(env):
    env.manager.invoke_hook("post_compaction", session_id="chat-A2", old_session_id="chat-A", kind="summarize",
                            reason="manual", before_tokens=1000, after_tokens=200, messages_removed=10)
    assert not env.db_path.exists()
    assert env.api_module.db.store_exists() is False
    env.start()
    assert env.api_module.db.store_exists() is True  # the check and the real store agree on the path


def test_two_exports_in_the_same_second_get_their_own_files(env):
    sid = _finish_with_one_attempt(env)
    first = env.api("POST", f"/studies/{sid}/export", {})[1]
    second = env.api("POST", f"/studies/{sid}/export", {})[1]
    assert first["path"] != second["path"]
    assert Path(first["path"]).read_text(encoding="utf-8") == first["markdown"]
    assert Path(second["path"]).read_text(encoding="utf-8") == second["markdown"]
    leftovers = [p.name for p in Path(first["path"]).parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions as a normal user")
def test_file_problems_are_reported_and_never_leave_half_a_record(env):
    sid = _finish_with_one_attempt(env)
    exports = env.db_path.parent / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    exports.chmod(0o555)
    try:
        code, body = env.api("POST", f"/studies/{sid}/export", {})
        assert code == 500 and body["error"] == "file_error" and "Your records are unchanged" in body["message"]
        assert env.rows("SELECT COUNT(*) AS n FROM events WHERE kind = 'exported'")[0]["n"] == 0
        assert _command(env)("export").startswith("Could not write or remove the export file")
    finally:
        exports.chmod(0o755)
    path = Path(env.api("POST", f"/studies/{sid}/export", {})[1]["path"])
    exports.chmod(0o555)  # the file cannot be unlinked now
    try:
        code, req = env.api("POST", f"/studies/{sid}/delete-request", {})
        code, out = env.api("POST", f"/studies/{sid}/delete", {"confirm_token": req["confirm_token"]})
        assert code == 200 and out["exports_left"] == [str(path)]  # deleted, and the leftover file is named
        assert env.rows("SELECT COUNT(*) AS n FROM studies")[0]["n"] == 0
    finally:
        exports.chmod(0o755)


def test_a_busy_store_gets_the_plugins_message_in_the_text_door(env, monkeypatch):
    import sqlite3

    study = env.start()
    env.pose(study)
    handler = env.manager._plugin_commands["study"]["handler"]
    core = handler.__globals__["core"]

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(core, "submit_attempt", locked)
    assert _command(env)("answer 2x").startswith("Not saved: the Workbench storage is busy or unavailable.")


def test_a_retry_after_a_check_is_labelled_after_feedback(env):
    sid = env.start()["study_id"]
    a = env.plan(sid)["steps"][0]["step_id"]
    eid = env.pose_step(sid, a, hints=[])["exercise_id"]
    code, first = env.submit(sid, eid, "2x + h")
    assert first["help_class"] == "unaided"
    assert env.assess(sid, first["attempt_id"], "incorrect", "2x + h", gap="Let h go to 0 after cancelling.")["ok"]
    code, _ = env.api("POST", f"/studies/{sid}/exercises/{eid}/retry", {})
    assert code == 200
    code, second = env.submit(sid, eid, "2x")
    assert code == 200 and second["help_class"] == "after_feedback"


def test_every_help_class_has_a_label_in_every_door(env):
    core = env.api_module.core
    export = env.api_module.export
    commands = env.manager._plugin_commands["study"]["handler"].__globals__
    js = (REPO / "desktop" / "plugin.js").read_text(encoding="utf-8")
    js_help = set(re.findall(r"(\w+):", re.search(r"const HELP = \{([^}]*)\}", js).group(1)))
    assert set(core.HELP_CLASSES) == set(commands["HELP_LABEL"]) == set(export.HELP_LABEL) == js_help


def test_the_version_is_the_same_everywhere(env):
    manifest = (REPO / "plugin.yaml").read_text(encoding="utf-8")
    version = re.search(r"^version:\s*([\w.]+)", manifest, re.M).group(1)
    assert json.loads((REPO / "dashboard" / "manifest.json").read_text(encoding="utf-8"))["version"] == version
    code, health = env.api("GET", "/health")
    assert code == 200 and health["version"] == version
